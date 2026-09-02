"""MCP 工具接入测试：验证 MCP Tool 和本地 Tool 在 execute_tool_call 层面完全统一。

覆盖点:
    1. 启动后 MCP 工具被动态发现，schema 进 ALL_TOOLS、MCPTool 进 TOOL_REGISTRY
    2. MCP 工具走 execute_tool_call 得到正确结果（123 * 456 = 56088）
    3. 本地工具没被破坏，同样是 123 * 456 = 56088
    4. 多次调用复用同一个 ClientSession（不会每次重启 MCP Server 子进程）
    5. 从工作线程调用也正常（FastAPI 线程池场景）

运行方式（在 backend 目录下）：
    ..\\.venv\\Scripts\\python.exe tests\\test_mcp_tools.py
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_mcp_tools.py -v

不需要数据库 / Redis / 真实 LLM，只依赖 MCP stdio 子进程 + 工具调用主循环。
"""

import asyncio
import atexit
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent.mcp_client import (  # noqa: E402
    MCPTool,
    close_mcp,
    connect_calculator_server,
    discover_and_register_mcp_tools,
)
from app.agent.tool_calling import execute_tool_call  # noqa: E402
from app.tools import ALL_TOOLS, TOOL_REGISTRY  # noqa: E402

# 与 FastAPI lifespan 完全相同的两步：启动时连接 + 发现注册，退出时关闭
REGISTERED = None


def _startup() -> list[str]:
    global REGISTERED
    connect_calculator_server()
    REGISTERED = discover_and_register_mcp_tools(ALL_TOOLS, TOOL_REGISTRY)
    return REGISTERED


_startup()
atexit.register(close_mcp)


def _tool_call(name: str, arguments: dict, call_id: str = "call_1") -> dict:
    """构造一条模型侧的 tool_call 并走统一的 execute_tool_call 执行。"""
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments, ensure_ascii=False),
        },
    }


def _run_tool_call(tool_call: dict) -> str:
    """同步测试入里去跑异步 execute_tool_call（一次一个事件循环）。"""
    return asyncio.run(execute_tool_call(tool_call))


def test_mcp_tools_discovered_and_registered():
    """list_tools 发现的工具进了 ALL_TOOLS / TOOL_REGISTRY，且是 MCPTool 实例。"""
    assert "mcp_calculator" in REGISTERED
    assert isinstance(TOOL_REGISTRY["mcp_calculator"], MCPTool)

    schema = next(
        t for t in ALL_TOOLS if t["function"]["name"] == "mcp_calculator"
    )
    assert schema["type"] == "function"
    assert schema["function"]["description"]
    parameters = schema["function"]["parameters"]
    assert parameters["type"] == "object"
    assert set(parameters["required"]) == {"a", "b", "operation"}


def test_local_tool_not_broken():
    """本地工具仍然存在，且没有被 MCPTool 覆盖。"""
    assert "calculator" in TOOL_REGISTRY
    assert not isinstance(TOOL_REGISTRY["calculator"], MCPTool)


def test_mcp_multiply_through_execute_tool_call():
    """核心用例：MCP 工具经统一入口执行，123 * 456 = 56088。"""
    content = _run_tool_call(
        _tool_call("mcp_calculator", {"a": 123, "b": 456, "operation": "multiply"})
    )
    assert content == '"56088"', content


def test_local_multiply_through_execute_tool_call():
    """对照用例：本地工具经同一入口执行，结果一致。"""
    content = _run_tool_call(
        _tool_call("calculator", {"a": 123, "b": 456, "operation": "multiply"})
    )
    assert content == "56088", content


def test_session_reused_across_calls():
    """连续多次调用复用同一个 ClientSession（不重启 MCP Server 子进程）。"""
    tool = TOOL_REGISTRY["mcp_calculator"]
    for index in range(3):
        content = _run_tool_call(
            _tool_call(
                "mcp_calculator",
                {"a": 123, "b": 456, "operation": "multiply"},
                call_id=f"reuse_{index}",
            )
        )
        assert content == '"56088"', content
    assert TOOL_REGISTRY["mcp_calculator"] is tool
    assert tool.client is not None


def test_call_from_worker_thread():
    """FastAPI 线程池场景下（同步代码在子线程里）调用 MCP 工具同样正常。"""
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda index: _run_tool_call(
                    _tool_call(
                        "mcp_calculator",
                        {"a": 123, "b": 456, "operation": "multiply"},
                        call_id=f"thread_{index}",
                    )
                ),
                range(4),
            )
        )
    assert results == ['"56088"'] * 4, results


def test_await_from_any_event_loop():
    """异步入口：在任意事件循环里 await 都拿得到结果（跨循环不会挂死）。"""
    tool = TOOL_REGISTRY["mcp_calculator"]

    async def main():
        single = await tool(a=123, b=456, operation="multiply")
        gathered = await asyncio.gather(
            *(tool(a=index, b=456, operation="multiply") for index in (1, 2))
        )
        return single, gathered

    single, gathered = asyncio.run(main())
    assert single == "56088", single
    assert gathered == ["456", "912"], gathered


def test_mcp_error_returned_to_model():
    """MCP Server 内部捕获的错误以文本回传给模型，不让异常炸穿主循环。"""
    content = _run_tool_call(
        _tool_call("mcp_calculator", {"a": 1, "b": 0, "operation": "divide"})
    )
    assert content == '"Error: 不能除以 0"', content


if __name__ == "__main__":
    # 独立运行：不依赖 pytest，顺序跑完所有用例并打印关键信息
    print("注册的 MCP 工具:", REGISTERED)
    print("ALL_TOOLS:", [t["function"]["name"] for t in ALL_TOOLS])
    print("TOOL_REGISTRY:", sorted(TOOL_REGISTRY))

    for case in (
        test_mcp_tools_discovered_and_registered,
        test_local_tool_not_broken,
        test_mcp_multiply_through_execute_tool_call,
        test_local_multiply_through_execute_tool_call,
        test_session_reused_across_calls,
        test_call_from_worker_thread,
        test_await_from_any_event_loop,
        test_mcp_error_returned_to_model,
    ):
        case()
        print(f"[OK] {case.__name__}")

    close_mcp()
    atexit.unregister(close_mcp)
    print("[OK] 全部通过：MCP 与本地工具走同一条链路，123 * 456 = 56088")
