"""M0 探针配套：手工搭的「只支持 2025-11-25」的旧协议 MCP 服务器。

为什么不用 SDK 自带的服务端：mcp 2.1.1 的服务端是双时代的（dual-era），
无论 stateless_http 设成什么，它都会接受 2026-07-28。要验证「平台能不能接
还没迁移的存量服务器」，必须自己写一个只会老协议的服务端。

只实现最小可用集：initialize / notifications/initialized / tools/list / tools/call，
外加「不认识 server/discover」——这正是老服务器的真实行为。
"""

from __future__ import annotations

import argparse
import json
import uuid

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

PROTOCOL_VERSION = "2025-11-25"

TOOLS = [
    {
        "name": "legacy_echo",
        "description": "老服务器上的回显工具。",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    }
]


def _reply(req_id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


async def handle_mcp(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return _error(None, -32700, "Parse error")

    req_id = body.get("id")
    method = body.get("method")

    if method == "initialize":
        session_id = uuid.uuid4().hex
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "legacy-probe-server", "version": "0.1.0"},
                },
            },
            headers={"Mcp-Session-Id": session_id},
        )

    if method == "notifications/initialized":
        return Response(status_code=202)

    if method == "tools/list":
        return _reply(req_id, {"tools": TOOLS})

    if method == "tools/call":
        params = body.get("params") or {}
        arguments = params.get("arguments") or {}
        if params.get("name") != "legacy_echo":
            return _error(req_id, -32602, f"Unknown tool: {params.get('name')}")
        return _reply(
            req_id,
            {"content": [{"type": "text", "text": f"legacy echo: {arguments.get('text', '')}"}], "isError": False},
        )

    # server/discover、ping 等一律不认识 —— 老服务器的真实表现
    return _error(req_id, -32601, f"Method not found: {method}")


app = Starlette(routes=[Route("/mcp", handle_mcp, methods=["POST"])])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
