"""
MCPClientManager：MCP Server 连接生命周期 + 工具路由（适配 mcp 2.x）。

v2 把原来 StdioServerParameters + stdio_client + ClientSession 三层
合并成了一个 Client 对象：每个 server 对应一个 Client，用 AsyncExitStack
长期持有，不在每次 tool call 时重启子进程。

设计要点:
1. 对外暴露的接口和本地 tool 保持一致：
   - get_openai_tools_schema()：一份 OpenAI function-calling 格式的
     schema 列表，可以直接和本地 tool 的 ALL_TOOLS 合并
   - get_tool_registry()：{tool_name: async callable}，签名和本地
     tool 一致（**kwargs 调用），直接 TOOL_REGISTRY.update(...)
2. 错误统一转成 MCPToolError，接入 execute_tool_call 的统一容错逻辑。
3. 单个 server 连接失败不拖垮应用启动：记录进 get_unhealthy_servers()，
   其他 server 照常可用。

事件循环约束（v2 底层是 anyio 结构化并发）:
    - Client 上下文的进入（connect_all）和退出（close_all）必须发生在
      同一个任务里——FastAPI lifespan 天然满足（uvicorn 的 startup/
      shutdown 在同一个任务上驱动 lifespan 协程）；
    - call_tool 只要在同一条事件循环上即可，任意任务都可以 await。

依赖方向: 本模块只依赖 mcp 包内的 config / errors，不认识
ALL_TOOLS / TOOL_REGISTRY；tools/mcp_adapter 是唯一使用方，
agent 包对 MCP 零感知。
"""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client, MCPError, StdioServerParameters
from mcp.types import TextContent

from .config import MCPServerConfig, get_mcp_server_configs
from .errors import MCPToolError

logger = logging.getLogger(__name__)


class MCPClientManager:
    """管理多个 MCP Server 的连接（v2 API：每个 server 对应一个 Client 对象）。

    用法（例如 FastAPI 的 lifespan）::

        manager = MCPClientManager([
            MCPServerConfig(name="calculator", args=["mcp_servers/calculator_server.py"]),
        ])
        await manager.connect_all()
        ALL_TOOLS.extend(manager.get_openai_tools_schema())
        TOOL_REGISTRY.update(manager.get_tool_registry())
        ...
        await manager.close_all()
    """

    def __init__(self, server_configs: list[MCPServerConfig] | None = None):
        self._server_configs = (
            server_configs if server_configs is not None else get_mcp_server_configs()
        )
        self._clients: dict[str, Client] = {}
        self._tool_to_server: dict[str, str] = {}
        self._tool_descriptions: dict[str, str] = {}
        self._tool_schemas: dict[str, dict[str, Any]] = {}
        self._stack: AsyncExitStack | None = None
        # server name -> 失败原因（连接或工具发现失败都会记录）
        self._unhealthy: dict[str, str] = {}

    @property
    def connected_servers(self) -> list[str]:
        """当前已连接的 server 名列表。"""
        return list(self._clients)

    def get_unhealthy_servers(self) -> dict[str, str]:
        """连接失败的 server 及原因（启动容错的可查询状态）。"""
        return dict(self._unhealthy)

    async def connect_all(self) -> list[str]:
        """启动并连接所有配置的 MCP Server，返回成功连接的 server 名列表。

        应用启动时（lifespan）调用一次。单个 server 连接失败不抛出：
        记录进 unhealthy 状态后继续，保证其他 server 照常可用。
        """
        if self._stack is not None:
            return list(self._clients)

        if not self._server_configs:
            logger.info("没有启用的 MCP server，跳过连接")
            return []

        self._stack = AsyncExitStack()
        connected: list[str] = []
        for config in self._server_configs:
            try:
                await self._connect_one(config)
                connected.append(config.name)
            except Exception:  # noqa: BLE001
                self._unhealthy[config.name] = "连接失败"
                logger.exception("MCP server '%s' 连接失败，已跳过", config.name)
        return connected

    async def _connect_one(self, config: MCPServerConfig) -> None:
        # v2: Client 直接接收 StdioServerParameters，自动识别用子进程连接；
        # Client 本身就是 async context manager，用 AsyncExitStack 长期持有，
        # initialize 握手在 __aenter__ 里自动完成
        params = StdioServerParameters(
            command=config.command,
            args=config.resolved_args(),
            env=config.env,
        )
        client = Client(params)
        await self._stack.enter_async_context(client)
        self._clients[config.name] = client

        # 记录这个 server 提供了哪些工具，并缓存 schema
        result = await client.list_tools()
        for tool in result.tools:
            if tool.name in self._tool_to_server:
                logger.warning(
                    "工具名 '%s' 在多个 MCP server 中重复（'%s' 和 '%s'），后者将覆盖前者",
                    tool.name,
                    self._tool_to_server[tool.name],
                    config.name,
                )
            self._tool_to_server[tool.name] = config.name
            self._tool_descriptions[tool.name] = tool.description or ""
            # v2: inputSchema -> input_schema（snake_case）
            self._tool_schemas[tool.name] = tool.input_schema or {
                "type": "object",
                "properties": {},
            }

        self._unhealthy.pop(config.name, None)
        logger.info("MCP server '%s' 已连接", config.name)

    async def close_all(self) -> None:
        """应用关闭时调用：干净地断开所有连接并清空路由。

        必须和 connect_all 在同一个任务里调用（anyio 取消作用域约束），
        FastAPI lifespan 天然满足。
        """
        if self._stack is not None:
            try:
                await self._stack.aclose()
            except Exception:  # noqa: BLE001
                logger.exception("关闭 MCP 连接失败")
            self._stack = None
        self._clients.clear()
        self._tool_to_server.clear()
        self._tool_descriptions.clear()
        self._tool_schemas.clear()
        logger.info("MCP 连接已全部关闭")

    def get_openai_tools_schema(self) -> list[dict[str, Any]]:
        """返回 OpenAI function-calling 格式的 schema 列表。

        可以直接和本地 tool 的 ALL_TOOLS 列表合并（ALL_TOOLS + this）。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": self._tool_descriptions.get(tool_name, ""),
                    "parameters": self._tool_schemas.get(
                        tool_name, {"type": "object", "properties": {}}
                    ),
                },
            }
            for tool_name in self._tool_to_server
        ]

    def get_tool_registry(self) -> dict[str, Any]:
        """返回 {tool_name: callable} 字典，签名和本地 tool 保持一致。

        可以直接 TOOL_REGISTRY.update(...) 进去；注册进主循环后
        对 execute_tool_call 来说就是一个普通的 async 可调用对象。
        """
        return {
            tool_name: self._make_tool_caller(tool_name)
            for tool_name in self._tool_to_server
        }

    def _make_tool_caller(self, tool_name: str):
        async def _call(**kwargs) -> str:
            return await self.call_tool(tool_name, kwargs)

        return _call

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """执行一次 MCP 工具调用，返回文本结果。

        失败时抛出 MCPToolError，由 execute_tool_call 的统一容错逻辑
        捕获并转成错误 JSON 回传给模型。

        v2 行为要点:
        - 工具函数内部 raise（业务错误）不会在 server 侧炸穿协议层，
          而是体现在 result.is_error=True，错误文本已经在 result.content
          里——这是 MCP 协议故意的设计：让模型能"看到"错误并自己重试。
        - 只有协议层错误（工具不存在对应的 server、连接断开等）才会
          真正抛出 MCPError，需要 catch 并转换成 MCPToolError。
        """
        server_name = self._tool_to_server.get(tool_name)
        if server_name is None:
            raise MCPToolError(f"未找到工具 '{tool_name}' 对应的 MCP server")

        client = self._clients.get(server_name)
        if client is None:
            raise MCPToolError(f"MCP server '{server_name}' 未连接")

        try:
            result = await client.call_tool(tool_name, arguments)
        except MCPError as error:
            # 协议层错误（连接断开、能力不匹配等），带上对方给的原因
            raise MCPToolError(
                f"调用 MCP 工具 '{tool_name}' 失败: {error.message}"
            ) from error
        except Exception as error:  # noqa: BLE001
            raise MCPToolError(
                f"调用 MCP 工具 '{tool_name}' 时发生未知错误: {error}"
            ) from error

        # content 是内容块的 union，narrow 出 TextContent 再读 .text
        text_parts = [
            block.text for block in result.content if isinstance(block, TextContent)
        ]
        output = "\n".join(text_parts)

        if result.is_error:
            # 工具执行失败（业务异常），错误信息已经在 output 里
            raise MCPToolError(output or f"MCP 工具 '{tool_name}' 执行失败")

        return output


# 模块级单例：应用启动（lifespan）与工具适配层共用同一个 manager，
# 保证 MCP 连接"启动时建立、运行期间复用、关闭时释放"。
mcp_manager = MCPClientManager()
