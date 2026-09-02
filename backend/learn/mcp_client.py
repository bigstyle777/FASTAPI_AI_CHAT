import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# 用脚本自身位置拼出 server 绝对路径，避免依赖运行时的工作目录
SERVER_SCRIPT = Path(__file__).resolve().parent / "mcp_server_calculator.py"


async def main():
    server_parameters = StdioServerParameters(
        command=sys.executable,  # 用当前解释器启动子进程，避免 Windows 上 python 指向错误
        args=[str(SERVER_SCRIPT)],
    )

    async with stdio_client(server_parameters) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools_result = await session.list_tools()
            print(tools_result)

            result = await session.call_tool(
                "calculator",
                {
                    "a": 10,
                    "b": 20,
                    "operation": "add"
                }
            )
            print("result:", result)


if __name__ == "__main__":
    asyncio.run(main())


import asyncio

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agent.mcp_tool import MCPTool  # noqa: F401  # 见 app/agent/mcp_client.py 的 MCPTool（正式实现在那里）


async def main():

    server_params = StdioServerParameters(
        command="python",
        args=["mcp_servers/calculator_server.py"],
    )

    async with stdio_client(server_params) as (read, write):

        async with ClientSession(read, write) as session:

            await session.initialize()

            # ① MCP Server 提供了哪些 Tool？
            tools_result = await session.list_tools()

            # ② 转换成 LLM 能看的 Tool Schema
            openai_tools = []

            for tool in tools_result.tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    },
                })

            print("给 LLM 的 Tools:")
            print(openai_tools)

            # ③ 创建 MCP Tool Wrapper
            tool_registry = {}

            for tool in tools_result.tools:
                tool_registry[tool.name] = MCPTool(
                    session,
                    tool.name,
                )

            # ④ 像普通 Tool 一样调用
            result = await tool_registry["calculator"](
                a=10,
                b=20,
                operation="multiply",
            )
            print("结果:", result)
if __name__ == "__main__":
    asyncio.run(main())