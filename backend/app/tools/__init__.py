"""
工具层汇总

这里负责把 tools/ 目录下所有工具模块的 Schema 和注册表合并成一份。
新增工具时:
    1. 在 tools/ 下新建一个模块（参考 calculator.py）
    2. 把模块加进下方 _TOOL_MODULES 元组
其他代码无需改动，新工具会自动生效。
"""

from collections.abc import Callable

from . import calculator, weather, web_search

_TOOL_MODULES = (
    calculator,
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
