"""M0 探针配套测试服务器（临时文件，验证完可删）。

提供三种外部 MCP 服务器的模拟形态：
- stdio 模式：本地子进程形态（对应 npx/uvx 分发的 MCP 服务器）
- streamable-http + stateless：新规范形态（2026-07-28）
- streamable-http + stateful：老规范形态（2025-11-25，模拟尚未迁移的存量服务器）

并始终注册一个需要"中途问用户"的工具 probe_order，用于验证 MRTR。
"""

import argparse
from typing import Annotated

from pydantic import BaseModel

from mcp.server.mcpserver import (
    AcceptedElicitation,
    Elicit,
    ElicitationResult,
    MCPServer,
    Resolve,
)


class Quantity(BaseModel):
    copies: int


async def ask_quantity() -> Elicit[Quantity]:
    """解析器：向用户索要份数。数量参数不会出现在工具的 input_schema 里。"""
    return Elicit("要预订几份？", Quantity)


mcp = MCPServer("probe-server")


@mcp.tool()
def probe_echo(text: str) -> str:
    """回显输入，用于连通性验证。"""
    return f"echo: {text}"


@mcp.tool()
async def probe_order(
    item: str,
    quantity: Annotated[ElicitationResult[Quantity], Resolve(ask_quantity)],
) -> str:
    """预订指定物品，份数由用户中途确认（触发 MRTR / elicitation）。"""
    if isinstance(quantity, AcceptedElicitation):
        return f"已预订 {quantity.data.copies} 份 {item}"
    return f"未预订 {item}（用户拒绝或取消）"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", default="streamable-http")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="以有状态方式运行，让客户端协商到 2025-11-25（模拟未迁移的存量服务器）",
    )
    args = parser.parse_args()

    if args.transport == "streamable-http":
        # stateless_http=True  -> 2026-07-28
        # stateless_http=False -> 2025-11-25
        mcp.run(
            transport="streamable-http",
            port=args.port,
            stateless_http=not args.legacy,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
