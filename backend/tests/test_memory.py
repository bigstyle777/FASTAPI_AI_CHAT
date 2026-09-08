"""Memory 系统 Phase 1-4 测试。

分两块：

1. 纯逻辑测试（不需要数据库）
   - parse_extraction：JSON 解析、代码块容忍、噪声忽略
   - MemoryAction：字段校验与越界值钳制
   - build_conversation_text：长会话截断、本轮/上下文分段渲染
   - build_extraction_input：只留最近几条上下文 + 本轮，剔除 system
   - recency_score：半衰期指数衰减（reranker 模块）
   - 权重配置：每类型五因子各自和为 1.0
   - score_candidate / rerank：五因子加权、exactness 加分、中间明细
   - build_memory_context / compose_memory_context：按类型分块、回报真正被用上的 id

2. 数据库测试（依赖 conftest 的测试库）
   - upsert_semantic：CREATE 与 UPDATE 的区分
   - create_episodic：episodic 行有 embedding 字段
   - get / list / update / delete
   - retrieve_memories：mock embedding，三路合并排序，touch_accessed 与
     mark_accessed=False 的跳过

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
from app.memory import config as memory_config  # noqa: E402
from app.memory.extractor import (  # noqa: E402
    build_conversation_text,
    build_extraction_input,
    parse_extraction,
    render_known_memories,
)
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
    compose_memory_context,
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
from app.memory.status import ACTIVE, ARCHIVED  # noqa: E402
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


class TestExtractionInput:
    """提取输入只能是"最近几条上下文 + 本轮"。

    整段历史喂进去会让 episodic（一律 CREATE、不幂等）每轮重复产出；
    注入的记忆 system 消息喂回去则形成"提取自己记忆"的自我强化回路。
    """

    HISTORY = [
        {"role": "system", "content": "以下是关于该用户的记忆：偏好 4 空格缩进"},
        {"role": "user", "content": "第一轮问题"},
        {"role": "assistant", "content": "第一轮回答"},
        {"role": "user", "content": "第二轮问题"},
        {"role": "assistant", "content": "第二轮回答"},
        {"role": "user", "content": "就用它吧"},
    ]

    def test_focus_turn_is_last_two(self):
        result = build_extraction_input(
            self.HISTORY, user_text="就用它吧", assistant_text="好的，已选定方案 A"
        )
        assert result[-2] == {"role": "user", "content": "就用它吧"}
        assert result[-1] == {
            "role": "assistant",
            "content": "好的，已选定方案 A",
        }

    def test_system_messages_dropped(self):
        result = build_extraction_input(
            self.HISTORY, user_text="就用它吧", assistant_text="好的"
        )
        assert all(item["role"] != "system" for item in result)
        assert all("记忆" not in item["content"] for item in result)

    def test_current_turn_not_duplicated(self):
        """history 末尾已含本轮 user、甚至已就地 append 过本轮 assistant
        （旧代码里 modify_message 路径就会这样），都不能在文本里出现两次。"""
        history = [*self.HISTORY, {"role": "assistant", "content": "好的"}]
        result = build_extraction_input(
            history, user_text="就用它吧", assistant_text="好的"
        )
        assert sum(1 for item in result if item["content"] == "就用它吧") == 1
        assert sum(1 for item in result if item["content"] == "好的") == 1

    def test_context_capped(self):
        result = build_extraction_input(
            self.HISTORY, user_text="就用它吧", assistant_text="好的", max_context=2
        )
        # 2 条上下文 + 本轮 2 条
        assert len(result) == 4
        # 取的是最近的：第一轮被挤掉
        assert all("第一轮" not in item["content"] for item in result)

    def test_empty_assistant_not_padded(self):
        """生成被中断时不补空的 assistant 轮。"""
        result = build_extraction_input(
            self.HISTORY, user_text="就用它吧", assistant_text=""
        )
        assert result[-1] == {"role": "user", "content": "就用它吧"}

    def test_focus_section_rendered(self):
        conversation = build_extraction_input(
            self.HISTORY, user_text="就用它吧", assistant_text="好的"
        )
        text = build_conversation_text(conversation, focus_last=2)
        assert memory_config.EXTRACTION_FOCUS_HEADER in text
        assert memory_config.EXTRACTION_CONTEXT_HEADER in text
        # 本轮必须在上下文段之后
        assert text.index(memory_config.EXTRACTION_CONTEXT_HEADER) < text.index(
            memory_config.EXTRACTION_FOCUS_HEADER
        )

    def test_no_section_by_default(self):
        """focus_last 默认 0：不分段，保持旧的渲染行为。"""
        text = build_conversation_text([
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ])
        assert memory_config.EXTRACTION_FOCUS_HEADER not in text
        assert text == "用户：你好\n助手：你好呀"

    def test_render_skips_system(self):
        text = build_conversation_text([
            {"role": "system", "content": "注入的记忆"},
            {"role": "user", "content": "你好"},
        ])
        assert "注入的记忆" not in text


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

    def test_max_chars_keeps_top_ranked_only(self):
        """注入预算：超限时从低分往高分砍，但至少保住最高分那条。"""
        memories = [
            MemoryRead(
                id=1, user_id=1, type=MemoryType.SEMANTIC,
                key="top_fact", value="x" * 200,
                importance=0.9, confidence=0.9,
            ),
            MemoryRead(
                id=2, user_id=1, type=MemoryType.SEMANTIC,
                key="mid_fact", value="x" * 200,
                importance=0.5, confidence=0.9,
            ),
            MemoryRead(
                id=3, user_id=1, type=MemoryType.SEMANTIC,
                key="bottom_fact", value="x" * 200,
                importance=0.1, confidence=0.9,
            ),
        ]
        # 预算小到单条都放不下：fallback 保住最高分那条
        text = build_memory_context(memories, max_chars=80)
        assert "top_fact" in text
        assert "mid_fact" not in text
        assert "bottom_fact" not in text

    def test_max_chars_drops_lowest_when_partially_fits(self):
        memories = [
            MemoryRead(
                id=1, user_id=1, type=MemoryType.SEMANTIC,
                key="a", value="v" * 40,
                importance=0.9, confidence=0.9,
            ),
            MemoryRead(
                id=2, user_id=1, type=MemoryType.SEMANTIC,
                key="b", value="v" * 40,
                importance=0.8, confidence=0.9,
            ),
            MemoryRead(
                id=3, user_id=1, type=MemoryType.SEMANTIC,
                key="c", value="v" * 40,
                importance=0.1, confidence=0.9,
            ),
        ]
        # 预算只够前两条（各约 45+ 字符）：第三条（最低分）被砍
        text = build_memory_context(memories, max_chars=120)
        assert "- a：" in text and "- b：" in text
        assert "- c：" not in text


class TestComposeMemoryContext:
    """compose_memory_context 除了文本还要回报"哪几条真的进了 prompt"。

    这是 reinforce 的计数依据：被字符预算砍掉的记忆根本没被用上，
    给它们累加 access_count 会提前把状态推到 REINFORCED。
    """

    @staticmethod
    def _semantic(memory_id: int, key: str, value: str, importance: float = 0.9):
        return MemoryRead(
            id=memory_id, user_id=1, type=MemoryType.SEMANTIC,
            key=key, value=value, importance=importance, confidence=0.9,
        )

    def test_reports_all_ids_when_nothing_trimmed(self):
        memories = [self._semantic(1, "a", "甲"), self._semantic(2, "b", "乙")]
        context = compose_memory_context(memories)
        assert context.memory_ids == [1, 2]
        assert "- a：" in context.text and "- b：" in context.text

    def test_dropped_by_budget_are_not_reported(self):
        memories = [
            self._semantic(1, "a", "v" * 40, importance=0.9),
            self._semantic(2, "b", "v" * 40, importance=0.8),
            self._semantic(3, "c", "v" * 40, importance=0.1),
        ]
        context = compose_memory_context(memories, max_chars=120)
        assert context.memory_ids == [1, 2]
        assert 3 not in context.memory_ids

    def test_fallback_keeps_highest_ranked_id(self):
        """预算小到一条都放不下时，兜底保住的那条也要被报告。"""
        memories = [
            self._semantic(1, "top", "x" * 200, importance=0.9),
            self._semantic(2, "mid", "x" * 200, importance=0.5),
        ]
        context = compose_memory_context(memories, max_chars=80)
        assert context.memory_ids == [1]
        assert "top" in context.text

    def test_empty_input_reports_nothing(self):
        context = compose_memory_context([])
        assert context.text == ""
        assert context.memory_ids == []

    def test_zero_budget_reports_nothing(self):
        context = compose_memory_context([self._semantic(1, "a", "甲")], max_chars=0)
        assert context.text == ""
        assert context.memory_ids == []

    def test_build_memory_context_matches_compose_text(self):
        """薄封装不能和 compose 的文本走样。"""
        memories = [self._semantic(1, "a", "甲")]
        assert build_memory_context(memories) == compose_memory_context(memories).text


# ===========================================================================
# 数据库测试 —— 依赖 conftest 的测试库 + deterministic_embedding 假向量
# ===========================================================================


def _make_user(db, tag: str) -> int:
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    return create_user(db, f"{tag}_user", "hashed-password", role.id).id


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
        from _embedding_stub import fake_vector

        user_id = _make_user(db, "episodic")
        vector = fake_vector("用户决定改用 pgvector")
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
        from _embedding_stub import fake_vector

        user_id = _make_user(db, "epi-update")
        memory = create_episodic(
            db, user_id, "原文", event_type="decision",
            embedding=fake_vector("原文"),
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
        from _embedding_stub import fake_vector

        user_id = _make_user(db, "retrieve")
        # 同语义（共享相同文本前缀）：应该排第一
        target = create_episodic(
            db, user_id, "python fastapi 框架",
            embedding=fake_vector("python fastapi 框架"),
            importance=0.5,
        )
        # 不同语义：应该排后面
        noise = create_episodic(
            db, user_id, "中午吃了火锅",
            embedding=fake_vector("中午吃了火锅"),
            importance=0.5,
        )

        hits = retrieve_memories(db, "python fastapi", user_id, top_k=5)
        ids = [hit.id for hit in hits]
        assert target.id in ids
        # 第一个命中应该是 target（向量相似度 + 同等 importance 时排在前）
        assert hits[0].id == target.id

    def test_touch_accessed_updates_last_accessed_at(self, db):
        from _embedding_stub import fake_vector

        user_id = _make_user(db, "touch")
        target = create_episodic(
            db, user_id, "我要学的东西",
            embedding=fake_vector("我要学的东西"),
        )
        assert target.last_accessed_at is None

        before = datetime.utcnow() - timedelta(seconds=1)
        retrieve_memories(db, "我要学的东西", user_id, top_k=5)

        refreshed = get_by_id(db, target.id, user_id)
        assert refreshed.last_accessed_at is not None
        assert refreshed.last_accessed_at >= before

    def test_mark_accessed_false_skips_touch(self, db):
        """调用方接下来要 reinforce 时，检索层不应该再写一次库。

        reinforce_memories 自己就会刷 last_accessed_at 并累加 access_count，
        这里再 touch 一次就是对同一批 id 提交两个事务。
        """
        from _embedding_stub import fake_vector

        user_id = _make_user(db, "no-touch")
        target = create_episodic(
            db, user_id, "先不要碰我",
            embedding=fake_vector("先不要碰我"),
        )
        assert target.last_accessed_at is None

        hits = retrieve_memories(
            db, "先不要碰我", user_id, top_k=5, mark_accessed=False
        )

        assert [hit.id for hit in hits] == [target.id]  # 照样能检索到
        refreshed = get_by_id(db, target.id, user_id)
        assert refreshed.last_accessed_at is None  # 但没被标记
        assert refreshed.access_count == 0

    def test_empty_query_returns_empty(self, db):
        user_id = _make_user(db, "empty")
        assert retrieve_memories(db, "", user_id) == []

    def test_top_k_caps_results(self, db):
        from _embedding_stub import fake_vector

        user_id = _make_user(db, "topk")
        for i in range(5):
            create_episodic(
                db, user_id, f"事件 {i}",
                embedding=fake_vector(f"事件 {i}"),
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


# ===========================================================================
# 更正已有记忆（UPDATE / ARCHIVE）—— 用户推翻自己之前说法的唯一出口
# ===========================================================================


class TestCorrectionActions:
    """没有这两个动作，提取器对"我不用 X 了"只能回 IGNORE，错记忆永远删不掉。"""

    def test_parse_update_and_archive(self):
        raw = (
            '{"memories":[{"action":"UPDATE","type":"semantic","target_id":12,'
            '"value":"改用 Django"},'
            '{"action":"ARCHIVE","type":"episodic","target_id":15}]}'
        )
        actions = parse_extraction(raw)
        assert [a.action for a in actions] == [
            MemoryActionType.UPDATE,
            MemoryActionType.ARCHIVE,
        ]
        assert actions[0].target_id == 12
        assert actions[1].target_id == 15

    def test_archive_needs_only_target_id(self):
        """"这条不对了"本身就是完整意图，不该再要一份新正文。"""
        action = MemoryAction(
            action=MemoryActionType.ARCHIVE, type=MemoryType.SEMANTIC, target_id=7
        )
        assert action.is_usable() is True

    def test_update_without_target_id_is_unusable(self):
        """LLM 没指定改哪条 —— 宁可跳过，也不能当成新建。"""
        action = MemoryAction(
            action=MemoryActionType.UPDATE, type=MemoryType.SEMANTIC, value="Django"
        )
        assert action.is_usable() is False

    def test_archive_without_target_id_is_unusable(self):
        action = MemoryAction(action=MemoryActionType.ARCHIVE, type=MemoryType.EPISODIC)
        assert action.is_usable() is False

    def test_update_semantic_does_not_need_new_key(self):
        """key 沿用原来的，所以只要求有新 value。"""
        action = MemoryAction(
            action=MemoryActionType.UPDATE, type=MemoryType.SEMANTIC,
            target_id=3, value="Django",
        )
        assert action.is_usable() is True

    def test_upsert_still_requires_key(self):
        """新增动作没 key 就没法 upsert，这条约束不能因为加了 UPDATE 而松掉。"""
        action = MemoryAction(
            action=MemoryActionType.UPSERT, type=MemoryType.SEMANTIC, value="Django"
        )
        assert action.is_usable() is False


class TestRenderKnownMemories:
    def test_renders_id_for_targeting(self):
        memories = [
            MemoryRead(
                id=12, user_id=1, type=MemoryType.SEMANTIC,
                key="backend_framework", value="FastAPI",
            ),
            MemoryRead(
                id=15, user_id=1, type=MemoryType.EPISODIC,
                summary="决定用 pgvector", event_type="decision",
            ),
        ]
        text = render_known_memories(memories)
        assert "[id=12] backend_framework = FastAPI" in text
        assert "[id=15]" in text and "pgvector" in text

    def test_empty_renders_empty(self):
        assert render_known_memories([]) == ""
        assert render_known_memories(None) == ""


class TestApplyCorrections:
    """落库侧：改已有记忆，且不能改到别人的。"""

    def test_update_changes_existing_memory_in_place(self, db):
        user_id = _make_user(db, "corr-update")
        original = upsert_semantic(
            db, user_id, "backend_framework", "FastAPI", importance=0.6
        )

        applied = apply_actions(db, user_id, [
            MemoryAction(
                action=MemoryActionType.UPDATE,
                type=MemoryType.SEMANTIC,
                target_id=original.id,
                value="改用 Django",
                importance=0.9,
                confidence=0.95,
            )
        ])

        assert len(applied) == 1
        # 改的是同一条，不是新建一条 —— 否则库里会同时存在 FastAPI 和 Django
        all_rows = db.execute(
            select(Memory).where(Memory.user_id == user_id)
        ).scalars().all()
        assert len(all_rows) == 1
        assert all_rows[0].id == original.id
        assert all_rows[0].value == "改用 Django"
        assert all_rows[0].importance == 0.9
        # key 沿用，不另起一个
        assert all_rows[0].key == "backend_framework"

    def test_archive_retires_without_deleting(self, db):
        user_id = _make_user(db, "corr-archive")
        memory = upsert_semantic(db, user_id, "old_job", "在字节工作")

        applied = apply_actions(db, user_id, [
            MemoryAction(
                action=MemoryActionType.ARCHIVE,
                type=MemoryType.SEMANTIC,
                target_id=memory.id,
            )
        ])

        assert len(applied) == 1
        row = get_by_id(db, memory.id, user_id)
        # 归档 ≠ 删除：LLM 判断可能出错，留一条能找回的路
        assert row is not None
        assert row.status == ARCHIVED
        # 检索默认排除已归档的
        assert retrieve_memories(db, "字节", user_id) == []

    def test_archive_is_idempotent(self, db):
        user_id = _make_user(db, "corr-archive-twice")
        memory = upsert_semantic(db, user_id, "k", "v")
        action = MemoryAction(
            action=MemoryActionType.ARCHIVE,
            type=MemoryType.SEMANTIC,
            target_id=memory.id,
        )
        apply_actions(db, user_id, [action])
        first = get_by_id(db, memory.id, user_id).updated_at
        apply_actions(db, user_id, [action])
        second = get_by_id(db, memory.id, user_id)
        assert second.status == ARCHIVED
        assert second.updated_at == first  # 重复归档不该刷新时间戳

    def test_cannot_touch_another_users_memory(self, db):
        """LLM 给的 target_id 可能编错 —— 取不到就跳过，绝不能改到别人的。"""
        owner = _make_user(db, "corr-owner")
        attacker = _make_user(db, "corr-attacker")
        victim = upsert_semantic(db, owner, "secret", "原本的内容")

        applied = apply_actions(db, attacker, [
            MemoryAction(
                action=MemoryActionType.UPDATE,
                type=MemoryType.SEMANTIC,
                target_id=victim.id,
                value="被改坏了",
            ),
            MemoryAction(
                action=MemoryActionType.ARCHIVE,
                type=MemoryType.SEMANTIC,
                target_id=victim.id,
            ),
        ])

        assert applied == []
        untouched = get_by_id(db, victim.id, owner)
        assert untouched.value == "原本的内容"
        assert untouched.status == ACTIVE

    def test_missing_target_is_skipped_not_crashed(self, db):
        """指向一条已被删掉的记忆：跳过它，同批其它动作照常执行。"""
        user_id = _make_user(db, "corr-missing")
        applied = apply_actions(db, user_id, [
            MemoryAction(
                action=MemoryActionType.ARCHIVE,
                type=MemoryType.SEMANTIC,
                target_id=999999,
            ),
            MemoryAction(
                action=MemoryActionType.UPSERT,
                type=MemoryType.SEMANTIC,
                key="ok", value="这条该成功",
            ),
        ])
        assert len(applied) == 1
        assert applied[0].key == "ok"


class TestExtractionSeesKnownMemories:
    """端到端：提取时库里已有的记忆必须出现在提示词里，否则改不动它。"""

    @staticmethod
    def _capture_llm(sink):
        from types import SimpleNamespace

        class _CapturingLLM:
            def create(self, **kwargs):
                sink["messages"] = kwargs["messages"]
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content='{"memories":[]}')
                        )
                    ]
                )

        class _Client:
            def __init__(self):
                self.chat = SimpleNamespace(completions=_CapturingLLM())

        return _Client()

    def test_prompt_contains_known_memory_ids(self, db, monkeypatch):
        import app.memory.extractor as extractor_mod

        user_id = _make_user(db, "known-prompt")
        memory = upsert_semantic(
            db, user_id, "backend_framework", "FastAPI", importance=0.8
        )

        sink: dict = {}
        monkeypatch.setattr(
            extractor_mod,
            "get_client",
            lambda **kwargs: (self._capture_llm(sink), "test-model"),
        )

        extractor_mod.extract_memories_for_user(
            db,
            user_id,
            [{"role": "user", "content": "我不用 FastAPI 了，改用 Django"}],
        )

        content = sink["messages"][-1]["content"]
        # id 必须出现：UPDATE / ARCHIVE 全靠它定位到具体那一条
        assert f"[id={memory.id}]" in content
        assert "backend_framework = FastAPI" in content
        # 已知记忆要标成"不要从中重新提取"，否则会触发自我强化
        assert "不要从中重新提取" in content

    def test_extraction_does_not_count_as_usage(self, db, monkeypatch):
        """提取器读库 ≠ 用户用到了 —— 不能刷新 last_accessed_at。

        刷了的话 decay 的"冷落 90 天"判断就失准：一条从没被用户用过的
        记忆，会因为提取器每轮都看它一眼而永远显得"刚被用过"。
        """
        import app.memory.extractor as extractor_mod

        user_id = _make_user(db, "known-no-touch")
        memory = upsert_semantic(db, user_id, "lang", "用 Python 写后端")

        sink: dict = {}
        monkeypatch.setattr(
            extractor_mod,
            "get_client",
            lambda **kwargs: (self._capture_llm(sink), "test-model"),
        )

        extractor_mod.extract_memories_for_user(
            db, user_id, [{"role": "user", "content": "Python 后端怎么写"}]
        )

        assert get_by_id(db, memory.id, user_id).last_accessed_at is None
