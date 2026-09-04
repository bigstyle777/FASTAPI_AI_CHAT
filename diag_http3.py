"""带服务端日志的 SDK 连接复现：看清 SDK 请求了哪个路径、服务器回了什么。"""

import asyncio
import socket
import subprocess
import sys
import threading
import time

from mcp import Client

PORT = 8877


def wait_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def pump(proc: subprocess.Popen) -> None:
    for line in proc.stdout:  # type: ignore[union-attr]
        print(f"    [server] {line.rstrip()}")


async def connect(legacy: bool, label: str) -> None:
    port = PORT if not legacy else PORT + 1
    cmd = [
        sys.executable,
        "mcp_probe_server.py",
        "--transport",
        "streamable-http",
        "--port",
        str(port),
    ]
    if legacy:
        cmd.append("--legacy")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    t = threading.Thread(target=pump, args=(proc,), daemon=True)
    t.start()
    try:
        if not wait_port(port):
            print("  服务器没起来")
            return
        print(f"=== {label} ===")
        try:
            async with Client(f"http://127.0.0.1:{port}/mcp") as client:
                print(f"  协议版本: {client.protocol_version}")
                res = await client.list_tools()
                print(f"  工具: {[t.name for t in res.tools]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  失败: {type(exc).__name__}: {exc}")
    finally:
        proc.terminate()
        time.sleep(0.5)


async def main() -> None:
    await connect(legacy=False, label="现代服务器 stateless(2026-07-28)")
    print()
    await connect(legacy=True, label="老服务器 stateful(2025-11-25)")


if __name__ == "__main__":
    asyncio.run(main())
