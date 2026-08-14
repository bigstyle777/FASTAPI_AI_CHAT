"""
计算器工具

一个工具模块只需要提供两样东西:
    TOOLS         - 给模型看的 JSON Schema（模型据此决定调哪个工具、传什么参数）
    TOOL_REGISTRY - 给代码看的 name -> 函数 映射（真正执行时按名字找到函数）

新增工具时复制本文件，改掉函数、schema 和注册表即可。
"""


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


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "执行基本数学运算，例如加减乘除",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number", "description": "第一个数字"},
                    "b": {"type": "number", "description": "第二个数字"},
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "要执行的运算",
                    },
                },
                "required": ["a", "b", "operation"],
                "additionalProperties": False,
            },
        },
    }
]


TOOL_REGISTRY = {
    "calculator": calculator,
}
