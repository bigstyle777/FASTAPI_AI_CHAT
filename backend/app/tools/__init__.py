"""
工具层汇总

这里负责把 tools/ 目录下所有工具模块的 Schema 和注册表合并成一份。
新增工具时:
    1. 在 tools/ 下新建一个模块（参考 calculator.py）
    2. 把模块加进下方 _TOOL_MODULES 元组
其他代码无需改动，新工具会自动生效。
"""

from collections.abc import Callable

from . import calculator, memory_search, rag_search, weather, web_search

_TOOL_MODULES = (
    calculator,
    memory_search,
    rag_search,
    weather,
    web_search,
)  # 所有工具模块，新增工具时在这里加一行

# 所有工具的 JSON Schema，传给模型的 tools 参数
ALL_TOOLS: list[dict] = []

# name -> 函数 的全局注册表，工具调用主循环靠它找到要执行的函数
TOOL_REGISTRY: dict[str, Callable] = {}

for _module in _TOOL_MODULES:
    ALL_TOOLS.extend(_module.TOOLS)
    TOOL_REGISTRY.update(_module.TOOL_REGISTRY)


async def register_dynamic_tools() -> list[str]:
    """应用启动期（lifespan）调用：把 MCP 工具合并进 ALL_TOOLS / TOOL_REGISTRY。

    不放在模块顶层执行，是因为 MCP 连接要在 lifespan 的异步上下文里建立
    （Client 上下文的进入/退出必须在同一个任务里）；延迟 import 也让本模块
    对 mcp 保持零耦合（mcp_adapter 才是唯一衔接点）。返回成功注册的工具名列表。
    """
    from .mcp_adapter import register_mcp_tools

    return await register_mcp_tools(ALL_TOOLS, TOOL_REGISTRY)
