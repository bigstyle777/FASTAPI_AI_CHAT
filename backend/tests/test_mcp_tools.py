"""MCP 工具接入测试：验证 MCP Tool 和本地 Tool 在 execute_tool_call 层面完全统一。

覆盖点:
    1. 启动后 MCP 工具被动态发现，schema 进 ALL_TOOLS、可调用对象进 TOOL_REGISTRY
    2. MCP 工具走 execute_tool_call 得到正确结果（123 * 456 = 56088）
    3. 本地工具没被破坏，同样是 123 * 456 = 56088
    4. 多次调用复用同一个连接（不会每次重启 MCP Server 子进程）
    5. 同一连接上的并发调用（asyncio.gather）
    6. MCP Server 内部错误以文本回传给模型，不让异常炸穿主循环

运行方式（在 backend 目录下）：
    ..\\.venv\\Scripts\\python.exe tests\\test_mcp_tools.py
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_mcp_tools.py -v

事件循环说明:
    mcp 2.x 的 Client 上下文依赖 anyio 结构化并发——进入（connect_all）和
    退出（close_all）必须在同一个任务里。测试里用一个常驻 supervisor 任务
    完成"连接 → 分发各用例的协程 → 关闭"全流程，pytest 的同步用例通过
    run_async() 把协程提交给它执行；对生产代码而言，lifespan 天然满足
    同任务约束。

不需要数据库 / Redis / 真实 LLM，只依赖 MCP stdio 子进程 + 工具调用主循环。
"""

import asyncio
import atexit
import concurrent.futures
import json
import sys
import threading
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent.tool_calling import execute_tool_call  # noqa: E402
from app.mcp import mcp_manager  # noqa: E402
from app.tools import ALL_TOOLS, TOOL_REGISTRY, register_dynamic_tools  # noqa: E402

# ---------------------------------------------------------------------------
# supervisor 事件循环：连接、各用例协程、关闭都在同一个任务里执行
# ---------------------------------------------------------------------------
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()
_dispatch: asyncio.Queue = asyncio.Queue()  # 只在 supervisor 的循环里消费
_ready = threading.Event()
REGISTERED: list[str] = []


async def _supervisor() -> None:
    """常驻任务：先连接注册，再依次执行提交进来的协程，最后干净关闭。

    关闭分支保证 future 一定会被置位、close_all 的异常一定被吞掉：
    atexit 阶段日志流可能已被 pytest 关闭（写日志会抛错），如果让
    它炸穿 supervisor，future.result() 会永久阻塞，进程就退不出去了。
    """
    REGISTERED.extend(await register_dynamic_tools())
    _ready.set()
    while True:
        future, coro = await _dispatch.get()
        if coro is None:  # 关闭信号
            try:
                await mcp_manager.close_all()
            except Exception:  # noqa: BLE001
                pass
            future.set_result(None)
            return
        try:
            future.set_result(await coro)
        except BaseException as error:  # noqa: BLE001
            future.set_exception(error)


def run_async(coro):
    """把协程交给 supervisor 任务执行并同步等待结果。

    通过 call_soon_threadsafe + asyncio.Queue 投递，不占任何线程池
    线程——如果用 asyncio.to_thread(_queue.get) 之类的同步队列等待，
    会把 concurrent.futures 的 worker 永久停在队列上，解释器退出时
    它的 atexit 会 join 这个非 daemon 线程，进程就挂死了。
    """
    future: concurrent.futures.Future = concurrent.futures.Future()
    _loop.call_soon_threadsafe(_dispatch.put_nowait, (future, coro))
    return future.result()


_closed = threading.Event()


def _shutdown() -> None:
    if _closed.is_set():
        return
    _closed.set()
    try:
        run_async(None)
    except Exception:  # noqa: BLE001
        pass  # atexit 阶段的任何异常都不该拦住进程退出


asyncio.run_coroutine_threadsafe(_supervisor(), _loop)
assert _ready.wait(timeout=60), "MCP supervisor 启动超时"
atexit.register(_shutdown)


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
    """同步测试入口里去跑异步 execute_tool_call（交给 supervisor 任务）。"""
    return run_async(execute_tool_call(tool_call))


def test_mcp_tools_discovered_and_registered():
    """list_tools 发现的工具进了 ALL_TOOLS / TOOL_REGISTRY。"""
    assert "mcp_calculator" in REGISTERED
    assert "mcp_calculator" in TOOL_REGISTRY

    schema = next(
        t for t in ALL_TOOLS if t["function"]["name"] == "mcp_calculator"
    )
    assert schema["type"] == "function"
    assert schema["function"]["description"]
    parameters = schema["function"]["parameters"]
    assert parameters["type"] == "object"
    assert set(parameters["required"]) == {"a", "b", "operation"}


def test_local_tool_not_broken():
    """本地工具仍然存在，且没有被 MCP 工具覆盖。"""
    assert "calculator" in TOOL_REGISTRY
    assert TOOL_REGISTRY["calculator"] is not TOOL_REGISTRY["mcp_calculator"]


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
    """连续多次调用复用同一个连接（不重启 MCP Server 子进程）。"""
    for index in range(3):
        content = _run_tool_call(
            _tool_call(
                "mcp_calculator",
                {"a": 123, "b": 456, "operation": "multiply"},
                call_id=f"reuse_{index}",
            )
        )
        assert content == '"56088"', content
    assert mcp_manager.connected_servers, "MCP server 应保持连接"
    assert mcp_manager.get_unhealthy_servers() == {}


def test_concurrent_calls_on_same_connection():
    """同一连接上并发调用（asyncio.gather），结果顺序与传入一致。"""
    tool = TOOL_REGISTRY["mcp_calculator"]

    async def body():
        return await asyncio.gather(
            *(tool(a=a, b=456, operation="multiply") for a in (1, 2, 3))
        )

    assert run_async(body()) == ["456", "912", "1368"]


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
        test_concurrent_calls_on_same_connection,
        test_mcp_error_returned_to_model,
    ):
        case()
        print(f"[OK] {case.__name__}")

    _shutdown()
    atexit.unregister(_shutdown)
    print("[OK] 全部通过：MCP 与本地工具走同一条链路，123 * 456 = 56088")
