# mcp_servers/calculator_server.py
# 适配 mcp 2.x：FastMCP 已更名为 MCPServer
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("calculator-server")


def calculator(a: float, b: float, operation: str) -> float:
    if operation == "add":
        return a + b
    if operation == "subtract":
        return a - b
    if operation == "multiply":
        return a * b
    if operation == "divide":
        if b == 0:
            raise ValueError("不能除以 0")
        return a / b
    raise ValueError(f"不支持的运算: {operation}")


@mcp.tool(
    name="calculator",
    description="执行基本数学运算，例如加减乘除",
)
def calculator_tool(a: float, b: float, operation: str) -> str:
    """执行基本数学运算

    Args:
        a: 第一个数字
        b: 第二个数字
        operation: 要执行的运算，可选 add / subtract / multiply / divide
    """
    try:
        return str(calculator(a, b, operation))
    except ValueError as e:
        # 错误也要以文本形式返回，而不是让异常炸穿协议层
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
