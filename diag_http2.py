"""最小复现：手工 POST 一个合规的 tools/list 到 /mcp，看状态码与响应体。"""

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = 8899
BASE = f"http://127.0.0.1:{PORT}"


def wait_port(port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def post(path: str, payload: bytes, legacy: bool) -> None:
    url = BASE + path
    version = "2025-11-25" if legacy else "2026-07-28"
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": version,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "replace")
            print(f"  [{path}] -> {resp.status}, {resp.headers.get('content-type')}")
            print(f"      body: {body[:400]}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        print(f"  [{path}] -> HTTP {exc.code}")
        print(f"      body: {body[:600]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [{path}] -> {type(exc).__name__}: {exc}")


def build_body(method: str, req_id: int, legacy: bool) -> bytes:
    import json

    msg: dict = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": {},
    }
    if not legacy:
        msg["params"]["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    return json.dumps(msg).encode()


def main() -> None:
    legacy = "--legacy" in sys.argv
    mode = "legacy(2025-11-25)" if legacy else "modern(2026-07-28)"
    print(f"=== 模式: {mode} ===")
    cmd = [
        sys.executable,
        "mcp_probe_server.py",
        "--transport",
        "streamable-http",
        "--port",
        str(PORT),
    ]
    if legacy:
        cmd.append("--legacy")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        if not wait_port(PORT):
            proc.kill()
            out, _ = proc.communicate(timeout=5)
            print("服务器没起来，输出：")
            print(out[-3000:])
            return
        print("端口已就绪，开始试探：")
        for path in ["/mcp", "/mcp/", "/", "/messages/"]:
            post(path, build_body("tools/list", 1, legacy), legacy)
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=5)
            tail = "\n".join(out.strip().splitlines()[-12:])
            if tail:
                print("--- 服务器日志尾部 ---")
                print(tail)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    main()
