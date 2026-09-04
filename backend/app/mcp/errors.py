"""MCP 包对外的异常类型。

整个 mcp 包对外只抛 MCPToolError：无论底层是协议错误、连接断开还是
工具执行失败，统一转成它，交给 agent 层 execute_tool_call 的
统一容错逻辑处理（转成错误 JSON 回传给模型）。
"""


class MCPToolError(Exception):
    """MCP 工具调用失败，由 execute_tool_call 的统一容错逻辑转成错误结果。"""
