"""Memory Consolidation（Phase 5）测试。

分两块：
1. 纯逻辑测试（不需要数据库，现在就能跑）
   - cosine_similarity：余弦相似度算术
   - cluster_episodes：按代表向量贪心聚类、单例过滤
   - _parse_summary：LLM 输出容错解析
2. 数据库测试（依赖 conftest 的测试库 + Postgres，起来后跑）
   - find_candidates：时间窗 + 相似度分簇
   - consolidate_group：新总结落库、原文标 CONSOLIDATED、溯源 metadata

用法（在 backend 目录下）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests/test_memory_consolidation.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.memory.consolidator import (  # noqa: E402
    ConsolidationResult,
    _parse_summary,
    cluster_episodes,
    consolidate_group,
    cosine_similarity,
    find_candidates,
)
from app.memory.models import Memory  # noqa: E402
from app.memory.repository import (  # noqa: E402
    consolidate_episodes,
    create_episodic,
    get_by_id,
)
from app.memory.schemas import MemoryType  # noqa: E402
from app.memory.status import CONSOLIDATED  # noqa: E402

DIMENSION = settings.rag_embedding_dimension


def _vector(*nonzero_indices: int) -> list[float]:
    """构造维度正确的稀疏向量：只在指定坐标上有值。

    _vector(0) 与 _vector(1) 互相正交（相似度 0），
    _vector(0) 与自身完全相同（相似度 1）——给聚类测试用。
    """
    vec = [0.0] * DIMENSION
    for index in nonzero_indices:
        vec[index % DIMENSION] = 1.0
    return vec


# ===========================================================================
# 纯逻辑
# ===========================================================================


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert cosine_similarity(_vector(0), _vector(0)) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert cosine_similarity(_vector(0), _vector(1)) == pytest.approx(0.0)

    def test_partial_overlap(self):
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        # dot=1, |a|=√2, |b|=1 → 1/√2 ≈ 0.707
        assert cosine_similarity(a, b) == pytest.approx(0.7071, abs=1e-3)

    def test_empty_returns_zero(self):
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0


class TestClusterEpisodes:
    def test_similar_items_form_group(self):
        items = [
            (1, _vector(0)),
            (2, _vector(0)),
            (3, _vector(0)),
        ]
        groups = cluster_episodes(items, threshold=0.78)
        assert groups == [[1, 2, 3]]

    def test_different_items_split(self):
        items = [
            (1, _vector(0)),
            (2, _vector(0)),
            (3, _vector(1)),  # 讲别的事
        ]
        groups = cluster_episodes(items, threshold=0.78)
        assert groups == [[1, 2]]  # (3) 是单例，不构成"重复"

    def test_singleton_excluded(self):
        items = [(1, _vector(0))]
        assert cluster_episodes(items, threshold=0.78) == []

    def test_order_preserved_within_group(self):
        items = [
            (10, _vector(0)),
            (20, _vector(0)),
            (30, _vector(0)),
        ]
        assert cluster_episodes(items, threshold=0.78) == [[10, 20, 30]]

    def test_threshold_respected(self):
        """cos ≈0.707 的两条，在 0.78 阈值下不该进同一组。"""
        a = [1.0, 1.0, 0.0]
        b = [1.0, 0.0, 0.0]
        assert cluster_episodes([(1, a), (2, b)], threshold=0.78) == []


class TestParseSummary:
    def test_plain_json(self):
        raw = (
            '{"summary":"用户多次抱怨魔法数字", "importance":0.8,'
            ' "confidence":0.9, "event_type":"preference"}'
        )
        parsed = _parse_summary(raw)
        assert parsed["summary"] == "用户多次抱怨魔法数字"
        assert parsed["importance"] == 0.8

    def test_markdown_fence_and_clamp(self):
        raw = (
            "```json\n"
            '{"summary":"归纳", "importance": 5, "confidence": -1,'
            ' "event_type":""}\n'
            "```"
        )
        parsed = _parse_summary(raw)
        assert parsed["importance"] == 1.0
        assert parsed["confidence"] == 0.0
        assert parsed["event_type"] == "insight"  # 空值兜底

    def test_empty_summary_raises(self):
        with pytest.raises(ValueError):
            _parse_summary('{"summary":"  ", "importance":0.5}')

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            _parse_summary("完全不是 JSON")


class TestConsolidateEpisodesDirect:
    """consolidate_episodes 的原子语义：<2 条或含非 active 时不落库。"""

    def test_too_few_returns_none(self, db, user_id):
        one = create_episodic(db, user_id, "唯一事件")
        merged = consolidate_episodes(
            db, user_id, [one.id], summary="不该有", importance=0.5,
            confidence=0.9, event_type="insight",
        )
        assert merged is None


# ===========================================================================
# 数据库测试（需要 Postgres，与其它 DB 用例一起在环境就绪后跑）
# ===========================================================================


def _seed_episode(db, user_id, summary, *, vector=None, days_ago=0, status="active"):
    memory = create_episodic(
        db,
        user_id,
        summary,
        event_type="complaint",
        importance=0.6,
        embedding=vector if vector is not None else _vector(0),
    )
    if days_ago:
        memory.created_at = datetime.utcnow() - timedelta(days=days_ago)
        memory.updated_at = memory.created_at
        db.commit()
    if status != "active":
        memory.status = status
        db.commit()
    return memory


_DEFAULT_SUMMARY = (
    '{"summary":"用户反复抱怨魔法数字（多次）", '
    '"importance":0.85, "confidence":0.9, "event_type":"preference"}'
)


class _FakeConsolidationLLM:
    """伪 LLM：返回一段写死的合并 JSON。

    payload 传 None 要按"没指定就用默认"处理，不能把 None 当成
    "返回空内容" —— FakeClient(payload=None) 是默认构造，那样会让所有
    期望合并成功的用例都因为 content=None 走到 llm_failed。
    """

    def __init__(self, summary=None):
        self._payload = _DEFAULT_SUMMARY if summary is None else summary

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=self._payload),
                )
            ]
        )


class FakeClient:
    """伪 OpenAI 客户端：chat.completions 走 _FakeConsolidationLLM。"""

    def __init__(self, payload=None):
        self.chat = SimpleNamespace(completions=_FakeConsolidationLLM(payload))


class TestFindCandidates:
    def test_groups_similar_episodes_in_window(self, db, user_id):
        same = _vector(0)
        different = _vector(1)
        _seed_episode(db, user_id, "讨厌魔法数字 1", vector=same)
        _seed_episode(db, user_id, "讨厌魔法数字 2", vector=same)
        _seed_episode(db, user_id, "讨厌魔法数字 3", vector=same)
        _seed_episode(db, user_id, "决定用 pgvector", vector=different)

        groups = find_candidates(db, user_id, window_days=7)

        assert len(groups) == 1  # 只有 3 条同类成组，另 1 条是单例
        assert len(groups[0]) == 3
        # 组内按事件发生顺序（入库顺序即 id 顺序）升序
        ids = [m.id for m in groups[0]]
        assert ids == sorted(ids)

    def test_outside_window_not_included(self, db, user_id):
        same = _vector(0)
        _seed_episode(db, user_id, "很久以前的抱怨", vector=same, days_ago=30)
        _seed_episode(db, user_id, "近期的抱怨", vector=same)

        groups = find_candidates(db, user_id, window_days=7)
        # 30 天前那条出窗，只剩一条在窗内，形不成组
        assert groups == []

    def test_consolidated_episodes_not_reused(self, db, user_id):
        same = _vector(0)
        first = _seed_episode(db, user_id, "旧重复 1", vector=same)
        second = _seed_episode(db, user_id, "旧重复 2", vector=same)

        # 模拟之前已被合并过：原文不再是 active
        merged = consolidate_episodes(
            db, user_id, [first.id, second.id],
            summary="已经合并过", importance=0.8, confidence=0.9,
            event_type="preference",
        )
        assert merged is not None

        # 再来两条同类新事件：只该跟"没被合并过"的比，
        # 已被标 CONSOLIDATED 的两条不能再次进组
        _seed_episode(db, user_id, "新的抱怨 1", vector=same)
        _seed_episode(db, user_id, "新的抱怨 2", vector=same)
        groups = find_candidates(db, user_id, window_days=7)
        assert len(groups) == 1
        assert len(groups[0]) == 2
        assert all("新的抱怨" in m.summary for m in groups[0])


class TestConsolidateGroup:
    def test_merges_and_keeps_traceability(self, db, user_id):
        same = _vector(0)
        episodes = [
            _seed_episode(db, user_id, "魔法数字又出现 1", vector=same),
            _seed_episode(db, user_id, "魔法数字又出现 2", vector=same),
        ]
        source_ids = sorted(m.id for m in episodes)

        result = consolidate_group(
            db,
            user_id,
            episodes,
            client=FakeClient(),
            model="test-model",
        )

        assert result.memory is not None, f"合并失败: {result.reason}"
        merged = result.memory
        assert merged.type == MemoryType.EPISODIC
        assert merged.source == "consolidation"
        assert "魔法数字" in merged.summary
        # 溯源：metadata 里记着原文 id
        assert merged.metadata_["source_episode_ids"] == source_ids
        assert merged.consolidated_into_id is None  # 新总结自身不再指向别人

        # 原文不物理删除，只是标记 CONSOLIDATED + 指向新总结
        for memory_id in source_ids:
            original = get_by_id(db, memory_id, user_id)
            assert original is not None, "合并后原文不应被物理删除"
            assert original.status == CONSOLIDATED
            assert original.consolidated_into_id == merged.id

    def test_llm_failure_skips_group_without_touching_originals(
        self, db, user_id
    ):
        same = _vector(0)
        episodes = [
            _seed_episode(db, user_id, "A", vector=same),
            _seed_episode(db, user_id, "B", vector=same),
        ]

        result = consolidate_group(
            db,
            user_id,
            episodes,
            client=FakeClient(payload="这不是 JSON"),
            model="test-model",
        )

        assert isinstance(result, ConsolidationResult)
        assert result.memory is None
        assert result.reason == "llm_failed"

        # 原文保持原状，可以再次尝试合并
        for memory in episodes:
            fresh = get_by_id(db, memory.id, user_id)
            assert fresh.status == "active"

    def test_less_than_two_skipped(self, db, user_id):
        one = _seed_episode(db, user_id, "只有一条")
        result = consolidate_group(
            db, user_id, [one], client=FakeClient(), model="test-model"
        )
        assert result.memory is None
        assert result.reason == "too_few"


# ---------------------------------------------------------------------------
# 汇总入口（Celery 用）需要真实 AI 配置才有 client；无配置时走 no_ai_config
# ---------------------------------------------------------------------------


def test_run_consolidation_without_ai_config_reports_gracefully(
    db, user_id, monkeypatch
):
    """用户没配 AI 时不能崩，要返回明确的跳过原因。"""
    from app.memory.consolidator import run_consolidation_for_user

    # user_id fixture 创建的用户没有 AI 设置。但 get_client 还会回落到
    # 全局配置（settings.deepseek_api_key / openai_api_key）—— 开发机上
    # 有 .env 时这两项是有值的，不显式清掉就走不到 no_ai_config 分支，
    # 这条用例会在"有 Key 的机器"上假失败。
    monkeypatch.setattr(settings, "deepseek_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)

    result = run_consolidation_for_user(db, user_id)
    assert result["consolidated"] == 0
    assert result.get("reason") == "no_ai_config"
