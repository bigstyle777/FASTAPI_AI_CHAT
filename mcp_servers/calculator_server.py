"""calculator MCP Server（stdio 传输）。

由 backend/app/agent/mcp_client.py 在应用启动时通过
StdioServerParameters 拉起，工具名使用 mcp_calculator，
避免与本地 calculator 工具重名冲突。

手动验证：
    python mcp_servers/calculator_server.py   # 以 stdio 方式运行，等待客户端连接
"""

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


def _format_result(value: float) -> str:
    """整数结果去掉多余的 .0，例如 56088.0 -> 56088"""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@mcp.tool(
    name="mcp_calculator",
    description="执行基本数学运算，例如加减乘除（MCP Server 提供）",
)
def mcp_calculator_tool(a: float, b: float, operation: str) -> str:
    """执行基本数学运算

    Args:
        a: 第一个数字
        b: 第二个数字
        operation: 要执行的运算，可选 add / subtract / multiply / divide
    """
    try:
        return _format_result(calculator(a, b, operation))
    except ValueError as e:
        # 错误以文本形式返回，而不是让异常炸穿协议层
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
