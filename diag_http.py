"""临时诊断：确认 streamable-http 服务器的实际端点与响应。"""

import json
import socket
import subprocess
import sys
import time
import urllib.request

port = 8791
proc = subprocess.Popen(
    [sys.executable, "mcp_probe_server.py", "--port", str(port)],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
)

ready = False
for _ in range(60):
    time.sleep(0.25)
    try:
        s = socket.create_connection(("127.0.0.1", port), 0.5)
        s.close()
        ready = True
        break
    except OSError:
        continue

print("服务器进程存活:", proc.poll() is None, "| 端口就绪:", ready)

if ready:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        }
    ).encode()
    for path in ["/", "/mcp", "/mcp/"]:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}{path}",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/list",
                },
            )
            r = urllib.request.urlopen(req, timeout=4)
            print(f"{path} -> {r.status} {r.read()[:200]!r}")
        except Exception as exc:
            detail = ""
            if hasattr(exc, "read"):
                try:
                    detail = exc.read()[:300].decode(errors="replace")
                except Exception:
                    detail = ""
            print(f"{path} -> 错误 {type(exc).__name__} code={getattr(exc, 'code', '')} {detail}")

proc.terminate()
try:
    out = proc.communicate(timeout=8)[0]
except subprocess.TimeoutExpired:
    proc.kill()
    out = proc.communicate()[0]
print("--- 服务器输出（尾部）---")
print((out or "")[-1500:])
