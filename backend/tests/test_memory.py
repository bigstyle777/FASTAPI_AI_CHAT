"""Memory 系统 Phase 1-4 测试。

分两块：

1. 纯逻辑测试（不需要数据库）
   - parse_extraction：JSON 解析、代码块容忍、噪声忽略
   - MemoryAction：字段校验与越界值钳制
   - build_conversation_text：长会话截断
   - recency_score：半衰期指数衰减（reranker 模块）
   - 权重配置：每类型五因子各自和为 1.0
   - score_candidate / rerank：五因子加权、exactness 加分、中间明细
   - build_memory_context：按类型分块

2. 数据库测试（依赖 conftest 的测试库）
   - upsert_semantic：CREATE 与 UPDATE 的区分
   - create_episodic：episodic 行有 embedding 字段
   - get / list / update / delete
   - retrieve_memories：mock embedding，三路合并排序，touch_accessed

用法（在 backend 目录下）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests/test_memory.py -v
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.admin.repository import create_role, get_role_by_name  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.memory.config import TYPE_WEIGHTS, weights_for  # noqa: E402
from app.memory.extractor import build_conversation_text, parse_extraction  # noqa: E402
from app.memory.manager import apply_actions  # noqa: E402
from app.memory.models import Memory  # noqa: E402
from app.memory.repository import (  # noqa: E402
    create_episodic,
    delete_memory,
    get_by_id,
    get_by_key,
    list_by_key,
    list_by_user,
    update_content,
    upsert_semantic,
)
from app.memory.retriever import (  # noqa: E402
    build_memory_context,
    retrieve_memories,
)
from app.memory.reranker import (  # noqa: E402
    Candidate,
    recency_score,
    rerank,
    score_candidate,
)
from app.memory.schemas import (  # noqa: E402
    MemoryAction,
    MemoryActionType,
    MemoryRead,
    MemoryType,
)
from app.user.repository import create_user  # noqa: E402

DIMENSION = settings.rag_embedding_dimension


# ===========================================================================
# 纯逻辑测试 —— 不需要数据库
# ===========================================================================


class TestParseExtraction:
    """LLM 输出格式多变，解析层要容忍各种包装和噪声。"""

    def test_plain_json(self):
        raw = '{"memories":[{"action":"UPSERT","type":"semantic","key":"k","value":"v"}]}'
        actions = parse_extraction(raw)
        assert len(actions) == 1
        assert actions[0].key == "k"
        assert actions[0].is_usable()

    def test_markdown_fence(self):
        raw = (
            "下面是结果：\n```json\n"
            '{"memories":[{"action":"CREATE","type":"episodic","summary":"s"}]}\n'
            "```\n"
        )
        assert len(parse_extraction(raw)) == 1

    def test_empty_memories_array(self):
        assert parse_extraction('{"memories":[]}') == []

    def test_empty_string(self):
        assert parse_extraction("") == []

    def test_garbage_returns_empty(self):
        assert parse_extraction("definitely not json") == []
        assert parse_extraction("无记忆") == []

    def test_missing_memories_key(self):
        assert parse_extraction('{"other": 1}') == []

    def test_ignore_is_not_usable(self):
        raw = (
            '{"memories":['
            '{"action":"IGNORE","type":"semantic","key":"k","value":"v"}'
            "]}"
        )
        actions = parse_extraction(raw)
        assert len(actions) == 1
        assert actions[0].action == MemoryActionType.IGNORE
        assert not actions[0].is_usable()

    def test_semantic_without_key_or_value_skipped(self):
        raw = (
            '{"memories":['
            '{"action":"UPSERT","type":"semantic","key":"k"},'
            '{"action":"UPSERT","type":"semantic","value":"v"}'
            "]}"
        )
        actions = parse_extraction(raw)
        assert all(not a.is_usable() for a in actions)

    def test_episodic_without_summary_skipped(self):
        raw = (
            '{"memories":[{"action":"CREATE","type":"episodic","event_type":"decision"}]}'
        )
        assert parse_extraction(raw)[0].is_usable() is False


class TestActionValidation:
    """LLM 偶尔输出越界数值，校验层要夹住而不是让整轮提取崩。"""

    def test_out_of_range_clamped(self):
        raw = (
            '{"memories":[{"action":"UPSERT","type":"semantic","key":"k","value":"v",'
            '"importance":8.5,"confidence":-2}]}'
        )
        action = parse_extraction(raw)[0]
        assert action.importance == 1.0
        assert action.confidence == 0.0

    def test_non_numeric_falls_back(self):
        raw = (
            '{"memories":[{"action":"UPSERT","type":"semantic","key":"k","value":"v",'
            '"importance":"huge"}]}'
        )
        action = parse_extraction(raw)[0]
        assert action.importance == 0.5  # 默认值兜底


class TestConversationTruncation:
    def test_truncate_from_tail(self):
        long_msg = "a" * 6000
        text = build_conversation_text([
            {"role": "user", "content": "开头一小段"},
            {"role": "assistant", "content": long_msg},
        ])
        assert len(text) <= settings.memory_extraction_max_chars
        # 从尾部截，所以"开头"应该被切掉
        assert "开头" not in text


class TestRecency:
    """时效性衰减：1 个半衰期前 = 0.5，2 个半衰期前 = 0.25（指数衰减）。"""

    def _make(self, **attrs):
        m = type("FakeMem", (), {})()
        for key, value in attrs.items():
            setattr(m, key, value)
        return m

    def test_half_life_decay(self):
        m = self._make(last_accessed_at=datetime.utcnow() - timedelta(days=30))
        score = recency_score(m, datetime.utcnow(), half_life_days=30.0)
        assert 0.49 < score < 0.51

    def test_two_half_lives(self):
        m = self._make(last_accessed_at=datetime.utcnow() - timedelta(days=60))
        score = recency_score(m, datetime.utcnow(), half_life_days=30.0)
        assert 0.24 < score < 0.26

    def test_fresh(self):
        m = self._make(last_accessed_at=datetime.utcnow())
        score = recency_score(m, datetime.utcnow(), half_life_days=30.0)
        assert 0.99 < score <= 1.0

    def test_falls_back_to_created_at_when_no_access(self):
        m = self._make(
            last_accessed_at=None,
            created_at=datetime.utcnow() - timedelta(days=15),
        )
        score = recency_score(m, datetime.utcnow(), half_life_days=30.0)
        # 0.5 ** (15/30) ≈ 0.707
        assert 0.7 < score < 0.72

    def test_unknown_decay_name_raises(self):
        m = self._make(last_accessed_at=datetime.utcnow())
        with pytest.raises(ValueError, match="未知的时效衰减函数"):
            recency_score(m, datetime.utcnow(), decay="不存在")

    def test_linear_decay_hits_half_at_half_life(self):
        """线性衰减：age = half_life 时正好 0.5，age = 2*half_life 时降到 0。"""
        m = self._make(last_accessed_at=datetime.utcnow() - timedelta(days=30))
        score = recency_score(
            m, datetime.utcnow(), half_life_days=30.0, decay="linear"
        )
        assert 0.49 < score < 0.51

        old = self._make(last_accessed_at=datetime.utcnow() - timedelta(days=60))
        assert recency_score(
            old, datetime.utcnow(), half_life_days=30.0, decay="linear"
        ) == 0.0

    def test_flat_decay_stays_one(self):
        """flat：时效性完全不衰减，新旧记忆 recency 都是 1.0。"""
        m = self._make(last_accessed_at=datetime.utcnow() - timedelta(days=365))
        assert recency_score(m, datetime.utcnow(), half_life_days=30.0, decay="flat") == 1.0


class TestTypeWeights:
    """每类型一套五因子权重：和为 1.0，episodic 无精确命中维度。"""

    def test_each_type_sums_to_one(self):
        for type_, weights in TYPE_WEIGHTS.items():
            total = sum(weights.values())
            assert abs(total - 1.0) < 1e-6, (
                f"{type_} 的权重之和为 {total}，应为 1.0"
            )

    def test_all_factors_covered(self):
        from app.memory.config import FACTORS

        for type_, weights in TYPE_WEIGHTS.items():
            assert set(weights) == set(FACTORS), (
                f"{type_} 的权重表应恰好覆盖 {FACTORS}，实际是 {sorted(weights)}"
            )

    def test_semantic_has_exactness_episodic_not(self):
        assert TYPE_WEIGHTS["semantic"]["exactness"] > 0
        assert TYPE_WEIGHTS["episodic"]["exactness"] == 0.0

    def test_episodic_recency_weighted_more_than_semantic(self):
        # episodic 是一次事件，越近越相关；semantic 是稳定事实，时效最不重要
        assert TYPE_WEIGHTS["episodic"]["recency"] > TYPE_WEIGHTS["semantic"]["recency"]

    def test_weights_for_unknown_type_raises(self):
        with pytest.raises(ValueError, match="未知记忆类型"):
            weights_for("不存在的类型")

    def test_weights_for_returns_copy(self):
        table = weights_for("semantic")
        table["importance"] = 0.0  # 改副本不应污染全局
        assert TYPE_WEIGHTS["semantic"]["importance"] > 0


class _ScoredFake:
    """reranker 单测用的假记忆：只需要打分公式读到的几个字段。"""

    def __init__(
        self,
        *,
        type: str = "semantic",
        importance: float = 0.5,
        confidence: float = 0.9,
        age_days: float = 0.0,
    ):
        self.type = type
        self.importance = importance
        self.confidence = confidence
        self.created_at = datetime.utcnow() - timedelta(days=age_days)
        self.last_accessed_at = self.created_at


class TestScoreCandidate:
    """五因子加权公式与中间明细：breakdown[f] = weights[f] * factors[f]。"""

    NOW = datetime.utcnow()

    def test_breakdown_matches_formula(self):
        mem = _ScoredFake(type="semantic", importance=0.8, confidence=0.9)
        scored = score_candidate(
            Candidate(mem, similarity=0.6, source="keyword"), self.NOW
        )
        assert scored.type == "semantic"
        assert scored.factors["similarity"] == 0.6
        assert scored.factors["importance"] == 0.8
        assert scored.factors["confidence"] == 0.9
        assert scored.factors["exactness"] == 0.0  # keyword 不算精确命中

        expected = {
            factor: scored.weights[factor] * scored.factors[factor]
            for factor in scored.factors
        }
        assert scored.breakdown == expected
        assert abs(scored.score - sum(expected.values())) < 1e-9

    def test_exact_key_sets_exactness_factor(self):
        mem = _ScoredFake(type="semantic", importance=0.5)
        exact = score_candidate(
            Candidate(mem, similarity=1.0, source="exact_key"), self.NOW
        )
        assert exact.factors["exactness"] == 1.0
        fuzzy = score_candidate(
            Candidate(mem, similarity=0.95, source="vector"), self.NOW
        )
        assert fuzzy.factors["exactness"] == 0.0
        # 同样的记忆，key 被精确命中必须比模糊语义命中得分更高
        assert exact.score > fuzzy.score

    def test_exact_key_not_overshadowed_by_fuzzy_semantic(self):
        """精确命中的正主（哪怕旧、importance 低）不能被模糊向量命中挤下去。

        这是 Phase 4 要守住的边界：用户都打出了完整 key，那条记录必须排前面，
        而不是被一条"语义上有点接近、但更常见更热门"的记忆盖过。
        """
        literal_hit = _ScoredFake(
            type="semantic", importance=0.4, confidence=0.9, age_days=30
        )
        fuzzy_hit = _ScoredFake(
            type="semantic", importance=0.9, confidence=0.9, age_days=0
        )
        results = rerank(
            [
                Candidate(fuzzy_hit, similarity=0.95, source="vector"),
                Candidate(literal_hit, similarity=1.0, source="exact_key"),
            ],
            now=self.NOW,
        )
        assert results[0].memory is literal_hit, (
            f"exact key 命中应排第一；实际第一是 fuzzy（"
            f"{results[0].factors['exactness']=}）"
        )
        assert results[0].factors["exactness"] == 1.0

    def test_episodic_exactness_weight_is_zero(self):
        # episodic 没有 key，exactness 因子恒 0，权重也是 0，任何来源都不加分
        mem = _ScoredFake(type="episodic", importance=0.5)
        scored = score_candidate(
            Candidate(mem, similarity=0.8, source="exact_key"), self.NOW
        )
        assert scored.weights["exactness"] == 0.0
        assert scored.breakdown["exactness"] == 0.0


class TestRerank:
    def test_sorts_desc_and_truncates_top_k(self):
        low = _ScoredFake(type="semantic", importance=0.2)
        high = _ScoredFake(type="semantic", importance=0.9)
        results = rerank(
            [
                Candidate(low, similarity=0.3, source="vector"),
                Candidate(high, similarity=0.3, source="vector"),
            ],
            now=datetime.utcnow(),
            top_k=1,
        )
        assert len(results) == 1
        assert results[0].memory is high

    def test_empty_input_returns_empty(self):
        assert rerank([], now=datetime.utcnow()) == []

    def test_episodic_old_important_beats_new_trivial_at_same_similarity(self):
        """episodic 权重下 importance 仍压得过 recency：90 天前高重要 > 刚发生琐碎。

        两条 similarity 相同，recency 只能拉回 (0.25 * (1 - 0.125)) ≈ 0.219，
        importance 差距有 0.30 * 0.85 = 0.255，旧高重要必须赢。
        """
        old_important = _ScoredFake(
            type="episodic", importance=0.95, confidence=0.95, age_days=90
        )
        new_trivial = _ScoredFake(
            type="episodic", importance=0.1, confidence=0.5, age_days=0
        )

        results = rerank(
            [
                Candidate(new_trivial, similarity=0.5, source="vector"),
                Candidate(old_important, similarity=0.5, source="vector"),
            ],
            now=datetime.utcnow(),
        )
        assert results[0].memory is old_important
        # episodic 权重表确实被采用（exactness 权重为 0）
        assert results[0].weights["exactness"] == 0.0


class TestBuildMemoryContext:
    """按类型分块：语义归"关于用户"，事件归"过去发生的事"。"""

    def test_separates_semantic_and_episodic(self):
        memories = [
            MemoryRead(
                id=1, user_id=1, type=MemoryType.SEMANTIC,
                key="code_indent", value="4 空格",
                importance=0.7, confidence=0.9,
            ),
            MemoryRead(
                id=2, user_id=1, type=MemoryType.EPISODIC,
                summary="改用 pgvector", event_type="decision",
                importance=0.9, confidence=0.9,
            ),
        ]
        text = build_memory_context(memories)
        assert "关于用户" in text and "code_indent" in text
        assert "过去发生的事" in text and "[decision]" in text

    def test_empty_returns_empty_string(self):
        assert build_memory_context([]) == ""


# ===========================================================================
# 数据库测试 —— 依赖 conftest 的测试库 + fake embedding
# ===========================================================================


def _make_user(db, tag: str) -> int:
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    return create_user(db, f"{tag}_user", "hashed-password", role.id).id


@pytest.fixture(autouse=True)
def fake_embedding(monkeypatch):
    """用确定性的 hash-based 向量，绕开真实 embedding API。

    不在这里定义 —— 直接复用 app/rag/embedding.py 已有的 _fallback_embedding。
    那个函数按 sha256 生成 deterministic 向量，对相同文本永远一致。
    """


class TestUpsertSemantic:
    def test_creates_then_updates(self, db):
        user_id = _make_user(db, "upsert")
        # CREATE
        memory = upsert_semantic(db, user_id, "code_indent", "2 空格")
        assert memory.value == "2 空格"
        # UPSERT：同 key 覆盖
        updated = upsert_semantic(db, user_id, "code_indent", "4 空格")
        assert updated.id == memory.id  # 同一行
        assert updated.value == "4 空格"
        # 数据库里仍然只有一条
        rows = db.execute(
            select(Memory).where(
                Memory.user_id == user_id, Memory.type == "semantic"
            )
        ).scalars().all()
        assert len(rows) == 1

    def test_unknown_user_returns_nothing(self, db):
        assert get_by_id(db, 99999, 1) is None


class TestGetByKey:
    """精确访问：不依赖 embedding，任何状态下都应该命中。"""

    def test_get_by_key_returns_exact_fact(self, db):
        user_id = _make_user(db, "getkey")
        upsert_semantic(db, user_id, "backend_framework", "FastAPI")

        memory = get_by_key(db, user_id, "backend_framework")
        assert memory is not None
        assert memory.value == "FastAPI"

    def test_get_by_key_missing_returns_none(self, db):
        user_id = _make_user(db, "getkey-miss")
        assert get_by_key(db, user_id, "不存在的 key") is None

    def test_get_by_key_scoped_to_user(self, db):
        user_a = _make_user(db, "getkey-a")
        user_b = _make_user(db, "getkey-b")
        upsert_semantic(db, user_a, "shared_key", "A 的值")
        assert get_by_key(db, user_b, "shared_key") is None

    def test_get_by_key_does_not_return_episodic(self, db):
        """episodic 没有 key，精确访问只面向 semantic。"""
        user_id = _make_user(db, "getkey-epi")
        create_episodic(db, user_id, "一次会议决定", event_type="decision")
        assert get_by_key(db, user_id, "一次会议决定") is None

    def test_list_by_key_filters_and_orders(self, db):
        user_id = _make_user(db, "listkey")
        upsert_semantic(db, user_id, "b_key", "2")
        upsert_semantic(db, user_id, "a_key", "1")
        upsert_semantic(db, user_id, "c_key", "3")
        create_episodic(db, user_id, "事件不该出现在 semantic 列表里")

        rows = list_by_key(db, user_id)
        assert [r.key for r in rows] == ["a_key", "b_key", "c_key"]

        filtered = list_by_key(db, user_id, keys=["a_key", "c_key"])
        assert [r.key for r in filtered] == ["a_key", "c_key"]


class TestCreateEpisodic:
    def test_create_with_embedding(self, db):
        from app.rag.embedding import _fallback_embedding

        user_id = _make_user(db, "episodic")
        vector = _fallback_embedding("用户决定改用 pgvector")
        memory = create_episodic(
            db,
            user_id,
            "用户决定改用 pgvector",
            event_type="decision",
            embedding=vector,
        )
        assert memory.type == "episodic"
        assert memory.event_type == "decision"
        assert memory.embedding is not None
        assert len(memory.embedding) == DIMENSION

    def test_update_episodic_clears_embedding(self, db):
        from app.rag.embedding import _fallback_embedding

        user_id = _make_user(db, "epi-update")
        memory = create_episodic(
            db, user_id, "原文", event_type="decision",
            embedding=_fallback_embedding("原文"),
        )
        updated = update_content(db, memory.id, user_id, "改了之后")
        assert updated.summary == "改了之后"
        # 原文改了，旧向量不再代表这条记忆，必须清空
        assert updated.embedding is None


class TestDelete:
    def test_delete_only_owners_memory(self, db):
        user_a = _make_user(db, "delete-a")
        user_b = _make_user(db, "delete-b")
        a = upsert_semantic(db, user_a, "k", "v")

        # user_b 删 user_a 的 —— 应该失败
        assert delete_memory(db, a.id, user_b) is False
        # user_a 自己删 —— 成功
        assert delete_memory(db, a.id, user_a) is True


class TestListByUser:
    def test_returns_all_types(self, db):
        user_id = _make_user(db, "list")
        upsert_semantic(db, user_id, "k1", "v1")
        create_episodic(db, user_id, "事件 A")
        create_episodic(db, user_id, "事件 B")

        all_memories = list_by_user(db, user_id)
        assert len(all_memories) == 3

        only_semantic = list_by_user(db, user_id, type=MemoryType.SEMANTIC)
        assert len(only_semantic) == 1
        only_episodic = list_by_user(db, user_id, type=MemoryType.EPISODIC)
        assert len(only_episodic) == 2


class TestRetrieveMemories:
    """混合检索的排序权重 + touch_accessed 用例。"""

    def test_vector_path_ranks_similar_above_unrelated(self, db):
        """fake embedding 让语义相近的 episodic 行排名靠前。"""
        from app.rag.embedding import _fallback_embedding

        user_id = _make_user(db, "retrieve")
        # 同语义（共享相同文本前缀）：应该排第一
        target = create_episodic(
            db, user_id, "python fastapi 框架",
            embedding=_fallback_embedding("python fastapi 框架"),
            importance=0.5,
        )
        # 不同语义：应该排后面
        noise = create_episodic(
            db, user_id, "中午吃了火锅",
            embedding=_fallback_embedding("中午吃了火锅"),
            importance=0.5,
        )

        hits = retrieve_memories(db, "python fastapi", user_id, top_k=5)
        ids = [hit.id for hit in hits]
        assert target.id in ids
        # 第一个命中应该是 target（向量相似度 + 同等 importance 时排在前）
        assert hits[0].id == target.id

    def test_touch_accessed_updates_last_accessed_at(self, db):
        from app.rag.embedding import _fallback_embedding

        user_id = _make_user(db, "touch")
        target = create_episodic(
            db, user_id, "我要学的东西",
            embedding=_fallback_embedding("我要学的东西"),
        )
        assert target.last_accessed_at is None

        before = datetime.utcnow() - timedelta(seconds=1)
        retrieve_memories(db, "我要学的东西", user_id, top_k=5)

        refreshed = get_by_id(db, target.id, user_id)
        assert refreshed.last_accessed_at is not None
        assert refreshed.last_accessed_at >= before

    def test_empty_query_returns_empty(self, db):
        user_id = _make_user(db, "empty")
        assert retrieve_memories(db, "", user_id) == []

    def test_top_k_caps_results(self, db):
        from app.rag.embedding import _fallback_embedding

        user_id = _make_user(db, "topk")
        for i in range(5):
            create_episodic(
                db, user_id, f"事件 {i}",
                embedding=_fallback_embedding(f"事件 {i}"),
            )
        hits = retrieve_memories(db, "事件", user_id, top_k=2)
        assert len(hits) <= 2


class TestApplyActions:
    """end-to-end：从提取结果落库。"""

    def test_skip_ignore(self, db):
        user_id = _make_user(db, "apply")
        actions = [
            MemoryAction(action=MemoryActionType.IGNORE, type=MemoryType.SEMANTIC,
                         key="x", value="y"),
            MemoryAction(action=MemoryActionType.UPSERT, type=MemoryType.SEMANTIC,
                         key="k", value="v", importance=0.7, confidence=0.9),
        ]
        applied = apply_actions(db, user_id, actions)
        assert len(applied) == 1
        assert db.execute(
            select(Memory).where(Memory.user_id == user_id)
        ).scalars().one().key == "k"

    def test_skip_missing_fields(self, db):
        user_id = _make_user(db, "apply-skip")
        # semantic 缺 value —— 应跳过
        actions = [
            MemoryAction(action=MemoryActionType.UPSERT, type=MemoryType.SEMANTIC, key="k"),
        ]
        assert apply_actions(db, user_id, actions) == []
