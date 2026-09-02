"""
stdio MCP Server 接入：把 MCP 工具适配成现有 tool calling 体系的一部分。

设计要点:
1. mcp 2.x 的高层 ``Client`` 一个对象就包办了"拉起 stdio 子进程 + 握手 +
   会话管理"，比 1.x 的 ``stdio_client + ClientSession + initialize`` 三层
   写起来短很多。应用启动时连一次，Agent 运行期间一直复用，
   不在每次 tool call 时重启子进程；应用关闭时断开。
2. MCP 的 stdio 读写依赖 anyio 的结构化并发（取消作用域必须在同一个任务里
   进出），所以用 BlockingPortal.wrap_async_context_manager 长期持有
   ``Client`` 上下文：进入和退出都发生在后台循环里，
   同步侧只需要像普通 context manager 一样使用。
3. MCPTool 的 ``__call__`` 是 async 的，直接放进 TOOL_REGISTRY；
   tool calling 主循环是 async 的，对 MCP 工具直接 ``await``。由于 stdio 读写
   绑定在常驻后台事件循环上，跨事件循环调用时通过
   ``asyncio.run_coroutine_threadsafe`` 把这次调用转交给后台循环执行。
   对主循环来说，MCP 工具和本地 tool 完全统一（都是 await 一个可调用对象）。

目前只支持单个 stdio MCP Server（mcp_servers/calculator_server.py），
不引入 HTTP/SSE、OAuth、自动重连、多 server 管理等复杂机制。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from anyio.from_thread import start_blocking_portal
from mcp import Client, MCPError, StdioServerParameters
from mcp.types import TextContent

logger = logging.getLogger(__name__)

# backend/app/agent/mcp_client.py -> backend/app/agent -> backend/app -> backend -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# 当前唯一接入的 MCP Server（stdio 方式启动）
CALCULATOR_SERVER_SCRIPT = "mcp_servers/calculator_server.py"

# 常驻后台事件循环门户：承载 stdio 子进程与 MCP 会话的读写
_portal = None  # anyio.from_thread.BlockingPortal
_portal_stop = None
# 门户所在的事件循环：stdio 流和会话都绑定在它上面，
# 跨事件循环直接 await 会挂死，所以调用时要识别出来并转交过去
_portal_loop: asyncio.AbstractEventLoop | None = None
# Client 上下文的同步包装（进入/退出在同一后台任务内完成）
_client_cm = None
# 已建立的客户端（单 server，保持简单）
_mcp_client: Client | None = None


class MCPToolError(Exception):
    """MCP 工具调用失败，由 execute_tool_call 的统一容错逻辑转成错误结果。"""


class MCPTool:
    """MCP 工具适配器：记住要调用的 MCP 工具名，通过常驻的 Client 去调它。

    放进 TOOL_REGISTRY 后，对 execute_tool_call 来说就是一个普通可调用对象：

    - ``await tool(**kwargs)``：真正的异步入口，内部执行
      ``await client.call_tool(tool_name, kwargs)``，
      并把返回的 TextContent 拼接成普通字符串。

    stdio 流和会话绑定在后台事件循环上，所以只有在后台循环里才能直接
    await；在别的事件循环里（比如 FastAPI 的异步接口）调用时，
    会自动把这次调用转交给后台循环执行，而不是挂死。
    """

    def __init__(self, client: Client, tool_name: str):
        self.client = client
        self.tool_name = tool_name

    async def _call(self, **kwargs) -> str:
        """真正发起 MCP 调用，必须在后台事件循环里执行。"""
        try:
            result = await self.client.call_tool(self.tool_name, kwargs)
        except MCPError as error:
            # 协议层错误（连接断开、能力不匹配等），带上对方给的原因
            raise MCPToolError(
                f"调用 MCP 工具 '{self.tool_name}' 失败: {error.message}"
            ) from error
        return _result_to_text(result, self.tool_name)

    async def __call__(self, **kwargs) -> str:
        current_loop = asyncio.get_running_loop()
        if _portal_loop is None or current_loop is _portal_loop:
            return await self._call(**kwargs)
        # 跨事件循环：交给后台循环执行，再把结果接回当前循环
        future = asyncio.run_coroutine_threadsafe(self._call(**kwargs), _portal_loop)
        return await asyncio.wrap_future(future)

    def __repr__(self) -> str:  # 便于日志/调试时一眼看出这是 MCP 工具
        return f"<MCPTool {self.tool_name}>"


def mcp_tool_to_openai_tool(tool) -> dict:
    """MCP Tool（name / description / input_schema）-> OpenAI function schema。

    mcp 2.x 里字段名是 input_schema（1.x 叫 inputSchema）。
    """
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.input_schema or {"type": "object", "properties": {}},
        },
    }


def _resolve_server_script(path: str) -> str:
    """相对路径按项目根目录解析成绝对路径，避免依赖运行时工作目录。"""
    script = Path(path)
    if not script.is_absolute():
        script = PROJECT_ROOT / script
    return str(script)


def _result_to_text(result, tool_name: str) -> str:
    """把 MCP 调用结果里的 TextContent 拼成普通字符串。

    工具函数内部抛的异常不会在这里 raise，而是体现在 result.is_error 上，
    错误文本已经在内容里了——这是 MCP 协议故意的设计，让模型能"看到"
    错误并自己修正参数重试。这里转成 MCPToolError，交给 execute_tool_call
    的统一容错逻辑处理。
    """
    text_parts = [
        block.text for block in result.content if isinstance(block, TextContent)
    ]
    output = "\n".join(text_parts)
    if result.is_error:
        raise MCPToolError(output or f"MCP 工具 '{tool_name}' 执行失败")
    return output


def _portal_run_blocking(coro) -> Any:
    """在 MCP 后台事件循环里阻塞执行一个协程。

    只用于**启动期**的一次性同步调用（如 list_tools 发现工具），
    主循环完全不经过这里——它直接 await MCPTool。
    """
    if _portal_loop is None:
        raise MCPToolError(
            "MCP 后台事件循环未启动，请先调用 connect_calculator_server()"
        )
    return asyncio.run_coroutine_threadsafe(coro, _portal_loop).result()


def connect_calculator_server(
    server_script: str = CALCULATOR_SERVER_SCRIPT,
) -> Client:
    """启动 stdio calculator MCP Server，返回复用的 Client。

    应用启动时（FastAPI lifespan）调用一次。
    """
    global _portal, _portal_stop, _portal_loop, _client_cm, _mcp_client
    if _mcp_client is not None:
        return _mcp_client

    params = StdioServerParameters(
        # 用当前解释器启动子进程，避免 Windows 上 python 指向错误
        command=sys.executable,
        args=[_resolve_server_script(server_script)],
    )

    portal_cm = start_blocking_portal(name="mcp-event-loop")
    portal = portal_cm.__enter__()

    client_cm = None
    try:
        # wrap_async_context_manager 保证异步上下文的进入/退出
        # 发生在后台循环的同一个任务里（anyio 结构化并发的要求）。
        # Client 自己会完成 initialize 握手，不用再手动调用。
        client_cm = portal.wrap_async_context_manager(Client(params))
        client = client_cm.__enter__()
    except BaseException:
        if client_cm is not None:
            try:
                client_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.exception("连接失败后清理 MCP 上下文出错")
        portal_cm.__exit__(None, None, None)
        raise

    _portal = portal
    _portal_stop = portal_cm.__exit__
    _portal_loop = portal.call(asyncio.get_running_loop)
    _client_cm = client_cm
    _mcp_client = client
    logger.info("MCP calculator server 已连接: %s", server_script)
    return client


def discover_and_register_mcp_tools(
    all_tools: list[dict],
    tool_registry: dict[str, Any],
) -> list[str]:
    """调用 client.list_tools() 动态发现工具，并注册进现有体系。

    - schema 追加进 all_tools（即 app.tools.ALL_TOOLS）
    - MCPTool 实例放进 tool_registry（即 app.tools.TOOL_REGISTRY）
    - 与已有本地工具重名时跳过，保证不覆盖本地 tool
    返回成功注册的工具名列表。
    """
    if _mcp_client is None:
        raise MCPToolError("MCP 未连接，请先调用 connect_calculator_server()")

    result = _portal_run_blocking(_mcp_client.list_tools())

    registered: list[str] = []
    for tool in result.tools:
        if tool.name in tool_registry:
            logger.warning(
                "MCP 工具 '%s' 与已有工具重名，已跳过（保留原工具）", tool.name
            )
            continue
        # MCPTool 实例本身就是可调用对象；异步主循环直接 await 它
        all_tools.append(mcp_tool_to_openai_tool(tool))
        tool_registry[tool.name] = MCPTool(_mcp_client, tool.name)
        registered.append(tool.name)

    logger.info("已注册 MCP 工具: %s", registered)
    return registered


def close_mcp() -> None:
    """应用关闭时调用：断开 MCP Server 连接并停止后台事件循环。"""
    global _portal, _portal_stop, _portal_loop, _client_cm, _mcp_client

    if _client_cm is not None:
        try:
            _client_cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.exception("关闭 MCP 连接失败")

    if _portal_stop is not None:
        try:
            _portal_stop(None, None, None)
        except Exception:  # noqa: BLE001
            logger.exception("停止 MCP 后台事件循环失败")

    _portal = None
    _portal_stop = None
    _portal_loop = None
    _client_cm = None
    _mcp_client = None
    logger.info("MCP 连接已关闭")
