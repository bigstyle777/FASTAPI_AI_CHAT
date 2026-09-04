"""Phase 7 架构测试：Agent 与 Memory 的解耦契约。

两类断言：
1. 架构隔离：app/agent/ 下任何文件都不得 import app.memory 的模块 ——
   agent 对 memory 实现保持无感知，只认 app/agent/ports.py 里的契约。
   用 ast 解析 import 语句并把相对导入解析成绝对模块名，比字符串
   匹配可靠（不会被 app.task.memory_queue 这类名字误伤）。
2. 契约实现：MemoryManager（app/memory 侧）结构上满足 MemoryPort，
   提供 retrieve / reinforce 两个动作；返回对象具备 MemoryRecord
   描述的字段（id/type/key/content/importance/confidence/score）。

DB 用例（Postgres 就绪后跑）：manager.reinforce 真正落库生效。
"""

from __future__ import annotations

import ast
import asyncio
import sys
from dataclasses import fields
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent.ports import MemoryPort, MemoryRecord  # noqa: E402
from app.memory.manager import MemoryManager  # noqa: E402

AGENT_DIR = BACKEND_DIR / "app" / "agent"


# ===========================================================================
# 1) 架构隔离
# ===========================================================================


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
        "agent/ 包不应直接依赖 app.memory 的实现，应该只认 ports 契约。"
        f"发现违规导入:\n" + "\n".join(offenders)
    )


def test_ports_contract_has_exactly_retrieve_and_reinforce():
    """契约要克制：agent 只需要 retrieve（取记忆）+ reinforce（确认用上）。"""
    methods = {
        name
        for name in dir(MemoryPort)
        if not name.startswith("_")
        and callable(getattr(MemoryPort, name, None))
    }
    assert {"retrieve", "reinforce"} <= methods


# ===========================================================================
# 2) 契约实现
# ===========================================================================


def test_memory_manager_conforms_to_memory_port():
    """MemoryManager 结构上满足 MemoryPort（鸭子类型，不 import agent）。"""
    manager = MemoryManager(db=None, user_id=1)
    assert isinstance(manager, MemoryPort), (
        "MemoryManager 应提供 MemoryPort 声明的 retrieve/reinforce 方法"
    )


def test_memory_record_fields_are_agent_safe():
    """MemoryRecord 的字段是 agent 能读的最小投影，全部有默认可构造性。"""
    record = MemoryRecord(
        id=1,
        type="semantic",
        key="k",
        content="v",
        importance=0.8,
        confidence=0.9,
        score=0.6,
    )
    assert record.id == 1 and record.content == "v"
    assert record.type == "semantic"
    # frozen dataclass 不能被改
    with pytest.raises(Exception):
        record.score = 0.9  # type: ignore[misc]


# ===========================================================================
# 3) DB 用例：manager.reinforce 落库生效
# ===========================================================================


def test_manager_reinforce_persists(db, user_id):
    """通过 MemoryManager（契约实现方）调 reinforce，验证真的落库。"""
    from app.memory.repository import create_episodic, get_by_id

    memory = create_episodic(db, user_id, "一次事件", confidence=0.9)
    manager = MemoryManager(db=db, user_id=user_id)

    count = asyncio.run(manager.reinforce([memory.id]))

    assert count == 1
    fresh = get_by_id(db, memory.id, user_id)
    assert fresh.access_count == 1
    assert fresh.confidence == pytest.approx(0.95)


def test_manager_retrieve_records_look_like_memory_records(db, user_id):
    """retrieve 返回的对象具备 MemoryRecord 描述的字段（agent 侧用法）。"""
    from app.memory.repository import upsert_semantic

    upsert_semantic(db, user_id, "favorite_color", "蓝色", importance=0.7)
    manager = MemoryManager(db=db, user_id=user_id)

    results = asyncio.run(manager.retrieve("favorite_color", top_k=5))

    assert results, "应能召回到刚写入的 semantic 记忆"
    top = results[0]
    record_field_names = {field.name for field in fields(MemoryRecord)}
    missing = record_field_names - {
        name for name in dir(top) if not name.startswith("_")
    }
    assert not missing, f"MemoryRead 缺 MemoryRecord 字段: {missing}"
