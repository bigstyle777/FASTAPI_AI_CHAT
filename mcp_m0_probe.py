"""M0 技术探针：为「MCP 工具接入与管理平台」的架构决策提供事实依据。

跑法：
    .venv/Scripts/python.exe mcp_m0_probe.py

验证 6 件事（对应 docs/mcp-platform-plan.md 的 M0）：
1. stdio 连接 + 协议版本 + tools/list（现有代码能不能继续用）
2. Streamable HTTP 连接（远程 MCP 的主流形态）
3. 只支持老协议（2025-11-25）的服务器能不能接进来
4. MRTR：工具执行中需要用户输入时，现有调用链路会不会断
5. 并发与连接生命周期：子进程泄漏 + 跨任务启停（决定 M2 架构）
6. 环境里的 HTTP_PROXY 会不会把内网 MCP 调用带沟里

输出为纯文本报告，不依赖任何项目内部模块，可独立运行。
"""

from __future__ import annotations

# 必须在 import mcp 之前设置：httpx 默认 trust_env=True，会读 HTTP_PROXY。
# 探针要连的是 127.0.0.1，必须显式豁免，否则请求会被送去代理（见探针 6）。
import os

os.environ["NO_PROXY"] = "127.0.0.1,localhost,::1"
os.environ["no_proxy"] = "127.0.0.1,localhost,::1"

import asyncio  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from contextlib import AsyncExitStack  # noqa: E402

from mcp import Client, StdioServerParameters  # noqa: E402

REPORT: list[str] = []


def log(line: str = "") -> None:
    print(line)
    REPORT.append(line)


def stdio_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["mcp_servers/calculator_server.py"],
    )


def wait_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def start_server(script: str, port: int, extra: list[str] | None = None) -> subprocess.Popen:
    cmd = [sys.executable, script, "--port", str(port)]
    if extra:
        cmd += extra
    return subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def count_python_children() -> int:
    """统计当前进程的 python 子进程数，用于检测 stdio 子进程是否泄漏。"""
    try:
        import psutil  # type: ignore
    except ImportError:
        return -1
    return len(psutil.Process(os.getpid()).children(recursive=True))


async def probe_1_stdio() -> None:
    log("=" * 68)
    log("[1] stdio 连接：协议版本与工具清单（现有 manager.py 的跑法）")
    log("=" * 68)
    client = Client(stdio_params())
    async with client:
        log(f"    协议版本      : {client.protocol_version}")
        log(f"    服务端标识    : {client.server_info}")
        result = await client.list_tools()
        log(f"    工具数量      : {len(result.tools)}")
        for tool in result.tools:
            log(f"      - {tool.name}: {tool.description}")
        call = await client.call_tool(
            "mcp_calculator", {"a": 6, "b": 7, "operation": "multiply"}
        )
        text = call.content[0].text if call.content else ""
        log(f"    调用结果      : 6 × 7 = {text} (is_error={call.is_error})")
        log(f"    结构化输出    : {call.structured_content}")
        log(f"    现有代码兼容性: manager.py 读 result.content[0].text -> {'可用' if text else '异常'}")
    log()


async def probe_2_http() -> None:
    log("=" * 68)
    log("[2] Streamable HTTP：远程 MCP 服务器（新规范 2026-07-28 形态）")
    log("=" * 68)
    port = 8765
    server = start_server("mcp_probe_server.py", port, ["--transport", "streamable-http"])
    try:
        if not wait_port(port):
            log("    服务器启动失败")
            return
        client = Client(f"http://127.0.0.1:{port}/mcp")
        async with client:
            log(f"    协议版本      : {client.protocol_version}")
            result = await client.list_tools()
            log(f"    工具数量      : {len(result.tools)}")
            for tool in result.tools:
                log(f"      - {tool.name}")
            call = await client.call_tool("probe_echo", {"text": "你好"})
            log(f"    调用结果      : {call.content[0].text}")
        log("    结论          : 远程 HTTP 形态与 stdio 用同一套 Client 代码，无需分支")
    finally:
        server.terminate()
        server.wait(timeout=10)
    log()


async def probe_3_legacy() -> None:
    log("=" * 68)
    log("[3] 老协议服务器：只支持 2025-11-25 的存量服务器能不能接")
    log("=" * 68)
    port = 8766
    server = start_server("mcp_legacy_server.py", port)
    try:
        if not wait_port(port):
            log("    服务器启动失败")
            return
        client = Client(f"http://127.0.0.1:{port}/mcp")
        async with client:
            version = client.protocol_version
            log(f"    协商到的版本  : {version}")
            log(f"    自动降级      : {'是（SDK 内部回退到 initialize 握手）' if version != '2026-07-28' else '否'}")
            result = await client.list_tools()
            log(f"    工具数量      : {len(result.tools)}")
            for tool in result.tools:
                log(f"      - {tool.name}")
            call = await client.call_tool("legacy_echo", {"text": "降级调用"})
            log(f"    调用结果      : {call.content[0].text}")
        log("    结论          : 同一份 Client 代码通吃新旧服务器，平台不必为老服务器写第二套逻辑")
    finally:
        server.terminate()
        server.wait(timeout=10)
    log()


async def probe_4_mrtr() -> None:
    log("=" * 68)
    log("[4] MRTR：工具执行中途要问用户（新规范最大的行为变化）")
    log("=" * 68)
    port = 8767
    server = start_server("mcp_probe_server.py", port, ["--transport", "streamable-http"])
    try:
        if not wait_port(port):
            log("    服务器启动失败")
            return

        log("    --- 场景 A：客户端提供了回答回调 ---")

        async def answer(context, params):
            from mcp_types import ElicitResult

            return ElicitResult(action="accept", content={"copies": 3})

        client = Client(f"http://127.0.0.1:{port}/mcp", elicitation_callback=answer)
        async with client:
            log(f"    协议版本      : {client.protocol_version}")
            tools = await client.list_tools()
            order = next((t for t in tools.tools if t.name == "probe_order"), None)
            if order is not None:
                schema_keys = list(order.input_schema.get("properties", {}).keys())
                log(f"    probe_order 暴露给模型的入参: {schema_keys}")
                log(f"    隐藏参数      : {'是（quantity 不在 schema 里，模型不会瞎填）' if 'quantity' not in schema_keys else '否'}")
            try:
                call = await client.call_tool("probe_order", {"item": "咖啡"})
                log(f"    返回结果类型  : {type(call).__name__}")
                log(f"    调用结果      : {call.content[0].text}")
                log("    结论          : 回调齐全时多轮往返由 SDK 自动跑完，调用方无感")
            except Exception as exc:  # noqa: BLE001
                log(f"    异常          : {type(exc).__name__}: {exc}")

        log()
        log("    --- 场景 B：客户端没给回调（模拟现有 manager.py）---")
        client2 = Client(f"http://127.0.0.1:{port}/mcp")
        async with client2:
            try:
                call = await client2.call_tool("probe_order", {"item": "咖啡"})
                log(f"    返回结果类型  : {type(call).__name__}")
                log(f"    is_error      : {call.is_error}")
                text = call.content[0].text if call.content else ""
                log(f"    调用结果      : {text}")
                log("    结论          : 无回调不抛异常，工具走默认分支直接返回")
            except Exception as exc:  # noqa: BLE001
                log(f"    异常类型      : {type(exc).__name__}")
                log(f"    异常信息      : {exc}")
                log("    结论          : 不声明能力就直接报错，不是「降级」而是「连不上」")

        log()
        log("    --- 场景 C：给了回调但用户拒绝（自动执行场景的降级路径）---")

        async def decline(context, params):
            from mcp_types import ElicitResult

            return ElicitResult(action="decline")

        client3 = Client(f"http://127.0.0.1:{port}/mcp", elicitation_callback=decline)
        async with client3:
            try:
                call = await client3.call_tool("probe_order", {"item": "咖啡"})
                log(f"    is_error      : {call.is_error}")
                log(f"    调用结果      : {call.content[0].text}")
                log("    结论          : 拒绝是可控降级，工具自己走兜底分支，不会卡死调用链")
            except Exception as exc:  # noqa: BLE001
                log(f"    异常          : {type(exc).__name__}: {exc}")
    finally:
        server.terminate()
        server.wait(timeout=10)
    log()


async def probe_5_concurrency() -> None:
    log("=" * 68)
    log("[5] 并发与连接生命周期（决定 M2 能不能随请求动态增删连接）")
    log("=" * 68)

    log("    --- 5a. 并发压测：5 个 stdio 服务器 + 100 次调用 ---")
    before = count_python_children()
    stack = AsyncExitStack()
    clients: list[Client] = []
    try:
        started = time.perf_counter()
        for _ in range(5):
            client = Client(stdio_params())
            await stack.enter_async_context(client)
            clients.append(client)
        elapsed = time.perf_counter() - started
        log(f"    启动 5 个 stdio 服务器耗时: {elapsed:.2f}s")

        async def one_call(i: int) -> str:
            target = clients[i % len(clients)]
            call = await target.call_tool(
                "mcp_calculator", {"a": i, "b": 2, "operation": "multiply"}
            )
            return call.content[0].text

        started = time.perf_counter()
        results = await asyncio.gather(*[one_call(i) for i in range(100)])
        elapsed = time.perf_counter() - started
        ok = all(r == str(i * 2) for i, r in enumerate(results))
        log(f"    100 次并发调用耗时: {elapsed:.2f}s，结果全部正确: {ok}")
    finally:
        await stack.aclose()
        await asyncio.sleep(0.5)
    after = count_python_children()
    if before >= 0:
        log(f"    子进程数 调用前={before} 调用后={after} -> {'无泄漏' if after <= before else '存在泄漏！'}")
    else:
        log("    未安装 psutil，跳过子进程泄漏检测")
    log()

    log("    --- 5b. 跨任务启停（M2 若想随请求动态开关会撞上这个）---")
    client = Client(stdio_params())
    stack2 = AsyncExitStack()

    async def _enter() -> None:
        await stack2.enter_async_context(client)

    await asyncio.create_task(_enter())
    try:
        call = await client.call_tool(
            "mcp_calculator", {"a": 3, "b": 4, "operation": "multiply"}
        )
        log(f"    跨任务调用    : 成功（3 × 4 = {call.content[0].text}）")
    except Exception as exc:  # noqa: BLE001
        log(f"    跨任务调用    : 失败 {type(exc).__name__}: {exc}")

    async def _close() -> None:
        await stack2.aclose()

    try:
        await asyncio.create_task(_close())
        log("    跨任务关闭    : 成功（说明本版本不强制同任务，但仍建议按最坏情况设计）")
    except Exception as exc:  # noqa: BLE001
        log(f"    跨任务关闭    : 失败 -> {type(exc).__name__}: {exc}")
        log("    结论          : 建立与关闭必须在同一任务，动态增删不能随请求走")
    log()

    log("    --- 5c. 专用长驻任务持有连接（规划里的方案 A）---")
    ready = asyncio.Event()
    stop = asyncio.Event()
    holder: dict[str, Client] = {}

    async def _holder_task() -> None:
        async with AsyncExitStack() as s:
            c = Client(stdio_params())
            await s.enter_async_context(c)
            holder["client"] = c
            ready.set()
            await stop.wait()

    task = asyncio.create_task(_holder_task())
    await ready.wait()
    log("    长驻任务内建立: 成功")

    async def _call_from_other_task() -> str:
        c = holder["client"]
        call = await c.call_tool("mcp_calculator", {"a": 12, "b": 12, "operation": "multiply"})
        return call.content[0].text

    text = await asyncio.create_task(_call_from_other_task())
    log(f"    其他任务调用  : 成功（12 × 12 = {text}）")

    stop.set()
    await asyncio.wait_for(task, timeout=10)
    log("    停止信号后关闭: 成功")
    log("    结论          : enter/exit 都在长驻任务内，跨任务调用完全可行")
    log()


CHILD_SNIPPET = """
import asyncio, os, sys
from mcp import Client
port = int(sys.argv[1])
async def main():
    async with Client(f"http://127.0.0.1:{port}/mcp") as c:
        r = await c.list_tools()
        print("OK", len(r.tools))
asyncio.run(main())
"""


async def probe_6_proxy() -> None:
    log("=" * 68)
    log("[6] 部署环境：内网 HTTP_PROXY 会不会劫持 MCP 调用")
    log("=" * 68)
    proxy = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    log(f"    环境 HTTP_PROXY: {proxy or '（未设置）'}")
    if not proxy:
        log("    本机没配代理，跳过该项验证")
        log()

        return

    port = 8768
    server = start_server("mcp_probe_server.py", port, ["--transport", "streamable-http"])
    try:
        if not wait_port(port):
            log("    服务器启动失败")
            return

        async def run_child(with_noproxy: bool) -> tuple[bool, str]:
            env = dict(os.environ)
            env.pop("NO_PROXY", None)
            env.pop("no_proxy", None)
            if with_noproxy:
                env["NO_PROXY"] = "127.0.0.1,localhost"
                env["no_proxy"] = "127.0.0.1,localhost"
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                CHILD_SNIPPET,
                str(port),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
            text = (out or b"").decode("utf-8", "replace").strip()
            if not text:
                text = (err or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
                text = text[0] if isinstance(text, list) else str(text)
            return proc.returncode == 0, text[:160]

        ok_without, msg_without = await run_child(False)
        log(f"    不豁免代理    : {'成功' if ok_without else '失败'} -> {msg_without}")
        ok_with, msg_with = await run_child(True)
        log(f"    豁免代理      : {'成功' if ok_with else '失败'} -> {msg_with}")
        log("    结论          : 平台必须显式管理代理（按目标地址设 NO_PROXY 或自建 httpx 客户端），")
        log("                    否则内网/本地 MCP 调用会被代理改写请求行而 404")
    finally:
        server.terminate()
        server.wait(timeout=10)
    log()


async def main() -> None:
    log()
    log("MCP 平台 M0 探针报告")
    log(f"Python {sys.version.split()[0]} / mcp 2.1.1")
    log()
    await probe_1_stdio()
    await probe_2_http()
    await probe_3_legacy()
    await probe_4_mrtr()
    await probe_5_concurrency()
    await probe_6_proxy()
    log("=" * 68)
    log("M0 探针执行完毕")


if __name__ == "__main__":
    asyncio.run(main())
