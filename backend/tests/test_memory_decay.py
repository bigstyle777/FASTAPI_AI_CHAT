"""Memory 生命周期（Phase 6：Reinforce / Decay / Archive）测试。

全部需要测试库（conftest 的 aichat_test），Postgres 就绪后跑：
    ..\\.venv\\Scripts\\python.exe -m pytest tests/test_memory_decay.py -v

覆盖：
- reinforce：access_count / confidence 上调、DECAYING 拉回、达阈值
  提升 REINFORCED、归档记忆不受影响
- run_decay：过期直归档、冷落降级（只降低 importance）、观察窗归档、
  全程不物理删除
- 检索默认排除 ARCHIVED/CONSOLIDATED（keyword / get_by_key 路径），
  DECAYING 仍可检索
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.memory.decay import reinforce_memories, run_decay  # noqa: E402
from app.memory.repository import (  # noqa: E402
    create_episodic,
    get_by_id,
    get_by_key,
    keyword_search_semantic,
    upsert_semantic,
)
from app.memory.status import (  # noqa: E402
    ACTIVE,
    ARCHIVED,
    CONSOLIDATED,
    DECAYING,
    REINFORCED,
)
from app.memory import config as memory_config  # noqa: E402


def _backdate(memory, *, days, last_accessed_days=None):
    """把一条记忆整体回拨到过去（created/updated/last_accessed 一起）。"""
    past = datetime.utcnow() - timedelta(days=days)
    memory.created_at = past
    memory.updated_at = past
    if last_accessed_days is not None:
        memory.last_accessed_at = datetime.utcnow() - timedelta(
            days=last_accessed_days
        )
    else:
        memory.last_accessed_at = past
    return memory


# ===========================================================================
# reinforce
# ===========================================================================


class TestReinforce:
    def test_increments_access_and_confidence(self, db, user_id):
        memory = create_episodic(db, user_id, "一次决定", importance=0.6, confidence=0.9)

        count = reinforce_memories(db, user_id, [memory.id])

        assert count == 1
        fresh = get_by_id(db, memory.id, user_id)
        assert fresh.access_count == 1
        assert fresh.confidence == pytest.approx(0.95)  # 0.9 + 0.05
        assert fresh.last_accessed_at is not None
        assert fresh.status == ACTIVE  # 还没到提升阈值

    def test_promotes_to_reinforced_after_threshold(self, db, user_id):
        memory = create_episodic(db, user_id, "反复用到的偏好", confidence=0.9)
        threshold = memory_config.REINFORCE_ACCESS_THRESHOLD

        for _ in range(threshold):
            reinforce_memories(db, user_id, [memory.id])

        fresh = get_by_id(db, memory.id, user_id)
        assert fresh.access_count == threshold
        assert fresh.status == REINFORCED

    def test_rescues_decaying_memory(self, db, user_id):
        memory = create_episodic(db, user_id, "差点被归档的目标", confidence=0.8)
        memory.status = DECAYING
        db.commit()

        count = reinforce_memories(db, user_id, [memory.id])

        assert count == 1
        fresh = get_by_id(db, memory.id, user_id)
        assert fresh.status == ACTIVE  # 降级拉回，不再走归档流程

    def test_archived_memory_is_not_reinforced(self, db, user_id):
        memory = create_episodic(db, user_id, "已归档的内容", confidence=0.8)
        memory.status = ARCHIVED
        db.commit()

        count = reinforce_memories(db, user_id, [memory.id])

        assert count == 0
        fresh = get_by_id(db, memory.id, user_id)
        assert fresh.access_count == 0  # 未被触碰

    def test_consolidated_memory_is_not_reinforced(self, db, user_id):
        memory = create_episodic(db, user_id, "已合并进总结", confidence=0.8)
        memory.status = CONSOLIDATED
        db.commit()

        assert reinforce_memories(db, user_id, [memory.id]) == 0

    def test_confidence_caps_at_one(self, db, user_id):
        memory = create_episodic(db, user_id, "置信度已满", confidence=0.98)
        reinforce_memories(db, user_id, [memory.id])
        fresh = get_by_id(db, memory.id, user_id)
        assert fresh.confidence == 1.0  # 0.98 + 0.05 → 封顶 1.0


# ===========================================================================
# run_decay
# ===========================================================================


class TestRunDecay:
    def test_stale_low_importance_becomes_decaying(self, db, user_id):
        memory = upsert_semantic(db, user_id, "temp_fact", "过时的小事", importance=0.2)
        _backdate(memory, days=200)

        result = run_decay(db, user_id)

        assert result["decayed"] == 1
        fresh = get_by_id(db, memory.id, user_id)
        assert fresh.status == DECAYING

    def test_high_importance_not_decayed(self, db, user_id):
        memory = upsert_semantic(db, user_id, "life_goal", "转型 AI Agent", importance=0.95)
        _backdate(memory, days=200)

        result = run_decay(db, user_id)

        assert result["decayed"] == 0
        assert get_by_id(db, memory.id, user_id).status == ACTIVE

    def test_recently_accessed_not_decayed(self, db, user_id):
        memory = upsert_semantic(db, user_id, "recent", "刚用过", importance=0.2)
        _backdate(memory, days=200, last_accessed_days=1)

        result = run_decay(db, user_id)
        assert result["decayed"] == 0

    def test_observation_window_archives_decaying(self, db, user_id):
        memory = upsert_semantic(db, user_id, "old_trivia", "没人再问的旧事", importance=0.2)
        memory.status = DECAYING
        # 观察窗起点 = 进入 DECAYING 的时刻（updated_at），回拨到 45 天前
        past = datetime.utcnow() - timedelta(days=45)
        memory.updated_at = past
        db.commit()

        result = run_decay(db, user_id)

        assert result["observation_archived"] == 1
        fresh = get_by_id(db, memory.id, user_id)
        assert fresh.status == ARCHIVED

    def test_expired_memory_archived_directly(self, db, user_id):
        memory = upsert_semantic(db, user_id, "dated_offer", "限时信息", importance=0.6)
        memory.expires_at = datetime.utcnow() - timedelta(days=1)
        db.commit()

        result = run_decay(db, user_id)

        assert result["expired_archived"] == 1
        assert get_by_id(db, memory.id, user_id).status == ARCHIVED

    def test_no_physical_delete(self, db, user_id):
        memory = upsert_semantic(db, user_id, "archiving", "将被归档", importance=0.2)
        _backdate(memory, days=200)
        memory.expires_at = datetime.utcnow() - timedelta(days=1)
        db.commit()

        run_decay(db, user_id)

        assert get_by_id(db, memory.id, user_id) is not None  # 归档 ≠ 删除


# ===========================================================================
# 检索默认排除（Phase 6 起 ARCHIVED / CONSOLIDATED 不再返回）
# ===========================================================================


class TestRetrievalExclusion:
    def test_keyword_search_excludes_archived(self, db, user_id):
        upsert_semantic(db, user_id, "lang_py", "用户用 Python 写后端")
        archived = upsert_semantic(db, user_id, "lang_old", "用户用 Python 写过老项目")
        archived.status = ARCHIVED
        db.commit()

        hits = keyword_search_semantic(db, user_id, "python", limit=10)
        keys = {memory.key for memory, _ in hits}
        assert "lang_py" in keys
        assert "lang_old" not in keys

    def test_keyword_search_still_returns_decaying(self, db, user_id):
        memory = upsert_semantic(db, user_id, "dying_fact", "还在观察期的旧事")
        memory.status = DECAYING
        db.commit()

        hits = keyword_search_semantic(db, user_id, "观察期", limit=10)
        assert any(memory.id == m.id for m, _ in hits)

    def test_get_by_key_excludes_archived(self, db, user_id):
        upsert_semantic(db, user_id, "active_key", "可用的值")
        archived = upsert_semantic(db, user_id, "archived_key", "旧值")
        archived.status = ARCHIVED
        db.commit()

        assert get_by_key(db, user_id, "active_key") is not None
        assert get_by_key(db, user_id, "archived_key") is None
