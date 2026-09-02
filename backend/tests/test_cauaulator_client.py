"""
测试脚本：连接 calculator MCP server，验证工具发现和调用是否正常。

用法（在 backend 目录下运行）：
    ..\.venv\Scripts\python.exe tests\test_cauaulator_client.py

要求：
    pip install mcp --break-system-packages
    项目根目录下有 mcp_servers/calculator_server.py
"""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# 用项目根目录拼出 server 绝对路径，避免依赖运行时的工作目录
SERVER_SCRIPT = str(BACKEND_DIR.parent / "mcp_servers" / "calculator_server.py")

# MCP server 里注册的工具名（避开与本地 calculator 工具重名）
TOOL_NAME = "mcp_calculator"


async def run_case(session: ClientSession, label: str, arguments: dict):
    """调用一次 calculator 工具，并打印结果"""
    print(f"\n[case] {label}")
    print(f"  参数: {arguments}")
    result = await session.call_tool(TOOL_NAME, arguments)

    # result.content 是 TextContent 列表
    for block in result.content:
        if block.type == "text":
            print(f"  结果: {block.text}")

    # MCP 协议里，工具执行失败通常会体现在 isError 字段
    if getattr(result, "isError", False):
        print("  (标记为错误结果)")


async def main():
    server_params = StdioServerParameters(
        command=sys.executable,  # 用当前解释器启动子进程，避免 Windows 上 python 指向错误
        args=[SERVER_SCRIPT],
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. 初始化握手
            await session.initialize()
            print("✅ 已连接到 MCP Server 并完成初始化")

            # 2. 工具发现：确认 server 正确暴露了 calculator
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print(f"✅ 发现工具: {tool_names}")
            assert TOOL_NAME in tool_names, f"❌ {TOOL_NAME} 工具未被发现"

            # 3. 正常调用：加减乘除各测一次
            await run_case(session, "加法", {"a": 3, "b": 4, "operation": "add"})
            await run_case(session, "减法", {"a": 10, "b": 4, "operation": "subtract"})
            await run_case(session, "乘法", {"a": 6, "b": 7, "operation": "multiply"})
            await run_case(session, "除法", {"a": 20, "b": 5, "operation": "divide"})

            # 4. 边界/异常情况：除以 0，验证错误是否被正确捕获并转成文本返回
            await run_case(session, "除以 0（预期报错）", {"a": 1, "b": 0, "operation": "divide"})

            # 5. 非法 operation，验证 schema 之外的输入如何处理
            await run_case(
                session,
                "非法运算符（预期报错）",
                {"a": 1, "b": 2, "operation": "power"},
            )

            print("\n🎉 全部测试用例执行完毕")


if __name__ == "__main__":
    asyncio.run(main())

