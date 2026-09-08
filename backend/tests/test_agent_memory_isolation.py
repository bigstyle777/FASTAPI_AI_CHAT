"""架构隔离守卫：Agent 包不依赖 Memory 实现。

方案 A 移除了 MemoryPort / MemoryManager 这一套"超前抽象"（Agent 运行时
从未真正消费它）后，这里保留唯一有真实价值的守卫：app/agent/ 下任何文件
都不得 import app.memory 的实现。这是实际成立的架构不变量 —— agent 的
memory 衔接点在 chat/services 的 build_memory_messages 与 MCP 工具
search_memory，依赖方向本来就干净，不该退化。

用 ast 解析 import 语句并把相对导入解析成绝对模块名，比字符串匹配可靠
（不会被 app.task.memory_queue 这类名字误伤）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

AGENT_DIR = BACKEND_DIR / "app" / "agent"


def _absolute_import_targets(package_dotted: str, source: str) -> list[str]:
    """把文件里的 import 都解析成绝对模块名（相对导入按 package 换算）。

    规则：模块 package_dotted = "a.b.mod"，其所属包是 "a.b"。
    `from .x import y`（level=1）→ 当前包拼 x → "a.b.x"；
    `from ..x import y`（level=2）→ 上一级包拼 x → "a.x"。
    `import x`（level=0）→ 原样 "x"。
    """
    tree = ast.parse(source)
    targets: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_pkg = package_dotted.split(".")[:-1]  # 当前模块的包
            level = node.level or 0
            if level > 1:
                # 上溯 level-1 层
                module_pkg = module_pkg[: max(0, len(module_pkg) - (level - 1))]
            resolved = ".".join(
                module_pkg + ([node.module] if node.module else [])
            )
            targets.append(resolved)
    return targets


def test_agent_package_never_imports_memory_implementation():
    """app/agent/ 的全部代码不得 import app.memory（含相对导入）。"""
    offenders: list[str] = []

    for path in sorted(AGENT_DIR.rglob("*.py")):
        relative = path.relative_to(BACKEND_DIR / "app")
        package = ".".join(relative.with_suffix("").parts)

        targets = _absolute_import_targets(package, path.read_text(encoding="utf-8"))
        for target in targets:
            parts = target.split(".")
            # app.memory.* 及其任何子模块
            if parts[:2] == ["app", "memory"]:
                offenders.append(f"{package} -> {target}")

    assert not offenders, (
        "agent/ 包不应直接依赖 app.memory 的实现。"
        "agent 应只借助 chat/services 的 build_memory_messages 与 MCP 工具"
        "search_memory 间接使用记忆。"
        f"发现违规导入:\n" + "\n".join(offenders)
    )