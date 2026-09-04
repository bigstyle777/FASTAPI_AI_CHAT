"""Memory System MVP 验收测试（6 个场景）。

验证 Memory System 不是退化成"朴素向量 RAG"。

与最初设计稿的差异（按 app/memory 实际接口适配）：
- MemoryManager 的签名是 ``MemoryManager(db, user_id, session_id=None)``，
  user_id 在构造器里，retrieve 只收 (query, top_k)
- retrieve 返回 list[MemoryRead]（含 score），不是 list[Memory]
- 项目没装 pytest-asyncio，这里用 asyncio.run() 包同步测试函数，
  不引入新依赖
- "破坏 embedding"直接用 ORM 把 embedding 置 NULL（原稿的
  repository.corrupt_embedding 方法不存在）
- 场景六的自定义时间戳同样通过 ORM 回写 created_at / last_accessed_at
- 测试用户用 conftest 的 user_id fixture（真实外键），不是随机字符串

运行前提（当前环境缺，跑之前先补）：
    1. Postgres（docker compose up -d db 或本地服务），测试库 aichat_test
       会由 conftest 自动创建；跑一次 alembic upgrade head 建 memories 表
    2. 场景一需要 LLM API Key：用户设置里配，或 .env 的 deepseek_api_key；
       没有 key 时该用例自动 skip，不会假通过

分两块：
- TestRankingMath：纯逻辑验证场景五/六背后的排序算术，不需要数据库，
  现在就能跑
- 场景一~六：需要测试库，DB 起来后整体跑

用法（在 backend 目录下）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests/test_memory_acceptance.py -v
    ..\\.venv\\Scripts\\python.exe -m pytest tests/test_memory_acceptance.py -m llm -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.memory.manager import MemoryManager
from app.memory.models import Memory
from app.memory.retriever import _score
from app.memory.schemas import MemoryAction, MemoryActionType


def _run(coro):
    """在没有 pytest-asyncio 的环境里跑 MemoryManager 的 async 方法。"""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def manager(db, user_id):
    """用项目真实的依赖注入方式构造 MemoryManager。

    db / user_id 来自 conftest（独立测试库 + 真实 users 行）。
    session_id 传 None：这些场景不依赖"同会话"那一路检索。
    """
    return MemoryManager(db, user_id)


async def _seed(manager, actions: list[dict]) -> list:
    """把 action 灌进 manager.apply，跳过真实 LLM extraction。

    用于不测 extractor 本身、只测 retrieval / ranking 逻辑的场景。
    返回落库后的 MemoryRead 列表（带 id，方便场景六回写时间戳）。
    """
    parsed = [MemoryAction(**a) for a in actions]
    return await manager.apply(parsed)


def _backdate(db, memory_id: int, days: int) -> None:
    """把一条记忆的时间戳回拨（场景六用），同时回拨 last_accessed_at，
    避免 touch_accessed 的默认值干扰时效性计算。"""
    memory = db.get(Memory, memory_id)
    past = datetime.utcnow() - timedelta(days=days)
    memory.created_at = past
    memory.last_accessed_at = past
    db.commit()


def _corrupt_embedding(db, user_id: int, key: str) -> None:
    """把某条 semantic 记忆的向量清空，模拟 embedding 服务异常/未生成。"""
    memory = db.execute(
        select(Memory).where(
            Memory.user_id == user_id,
            Memory.type == "semantic",
            Memory.key == key,
        )
    ).scalar_one()
    memory.embedding = None
    db.commit()


# ===========================================================================
# 纯逻辑：排序算术（不需要数据库，当前环境即可验证）
# ===========================================================================


class _FakeMemory:
    def __init__(self, importance, age_days, half_life=30.0):
        self.importance = importance
        now = datetime.utcnow()
        self.created_at = now - timedelta(days=age_days)
        self.last_accessed_at = self.created_at
        self._half_life = half_life


class TestRankingMath:
    """场景五/六的算术内核：不连库，直接验证 _score 的相对关系。"""

    NOW = datetime.utcnow()

    def test_scenario5_importance_outranks_on_similarity_tie(self):
        """相似度打平时，importance 高的必须排前面。"""
        trivial = _FakeMemory(importance=0.1, age_days=0)
        decision = _FakeMemory(importance=0.95, age_days=0)

        for similarity in (0.0, 0.3, 0.6, 0.9):
            assert _score(decision, similarity, self.NOW) > _score(
                trivial, similarity, self.NOW
            ), f"similarity={similarity} 时 importance 加权未生效"

    def test_scenario6_old_high_importance_beats_new_trivial(self):
        """90 天前的高重要记忆 vs 刚发生的琐碎记忆：相似度接近时旧的要赢。"""
        old_goal = _FakeMemory(importance=0.95, age_days=90)
        new_trivial = _FakeMemory(importance=0.1, age_days=0)

        # "相似度接近"用同一档相似度对比；也允许新记忆略高一点
        for similarity in (0.2, 0.3, 0.4, 0.5):
            assert _score(old_goal, similarity, self.NOW) > _score(
                new_trivial, similarity, self.NOW
            ), f"similarity={similarity} 时 recency 压过了 importance"

    def test_recency_boundary_is_documented(self):
        """边界行为要清楚：新记忆只有在相似度明显更高时才该反超。

        半衰期 30 天下，importance 差 (0.95-0.1)*0.3 = 0.255，
        recency 差 0.2*(1-0.125) = 0.175，旧记忆净优势 ≈ 0.08，
        折算成相似度是 0.16。也就是说新记忆要高出 0.16 以上的相似度
        才会反超 —— 这是合理的让步，不是缺陷，但要有人知道这个数。
        """
        old_goal = _FakeMemory(importance=0.95, age_days=90)
        new_trivial = _FakeMemory(importance=0.1, age_days=0)

        # 旧记忆相似度 0，新记忆 0.16 时正好打平；超过就该新记忆赢
        tie_gap = 0.16
        old_score = _score(old_goal, 0.0, self.NOW)
        new_score = _score(new_trivial, tie_gap, self.NOW)
        assert abs(old_score - new_score) < 0.01
        assert _score(new_trivial, tie_gap + 0.05, self.NOW) > old_score


# ===========================================================================
# 场景一~六：需要测试库
# ===========================================================================


@pytest.mark.llm
def test_scenario1_noise_is_filtered(manager, db, user_id):
    """寒暄/纠错类对话不应该产生任何 memory（真实调用 LLM extractor）。"""
    from app.core.ai_client import get_client, get_user_ai_settings

    api_key, provider = get_user_ai_settings(user_id=user_id, db=db)
    if not get_client(api_key=api_key, provider=provider):
        pytest.skip("没有可用的 LLM API Key，无法验证 extractor 的真实筛选能力")

    noisy_conversation = [
        {"role": "user", "content": "谢谢"},
        {"role": "assistant", "content": "不客气"},
        {"role": "user", "content": "好的我知道了"},
        {"role": "user", "content": "等一下我看看"},
        {"role": "user", "content": "不对我说错了，是这个意思"},
    ]

    actions = _run(manager.extract(noisy_conversation))

    non_ignore = [a for a in actions if a.action != MemoryActionType.IGNORE]
    assert len(non_ignore) == 0, (
        f"预期寒暄内容全部被 IGNORE，实际产生了 {len(non_ignore)} 条 memory: "
        f"{non_ignore}"
    )


def test_scenario2_semantic_exact_match_survives_broken_embedding(manager, db, user_id):
    """即使 embedding 被清空，semantic 的 key/value 匹配仍应召回。

    注意查询里带上了 value 的字面（FastAPI）—— key/value 匹配是字面级的，
    完全的改述召回靠的是 semantic 的 value 向量（见 upsert_semantic 的
    docstring），两者是互补关系，这条测的是"没有向量也必须能查"。
    """
    _run(
        _seed(
            manager,
            [
                {
                    "action": "UPSERT",
                    "type": "semantic",
                    "key": "backend_framework",
                    "value": "FastAPI",
                    "importance": 0.8,
                    "confidence": 0.95,
                }
            ],
        )
    )
    _corrupt_embedding(db, user_id, "backend_framework")

    results = _run(manager.retrieve("FastAPI 做后端怎么样", top_k=5))

    matched = [
        m for m in results
        if getattr(m, "key", None) == "backend_framework" and m.value == "FastAPI"
    ]
    assert matched, (
        "embedding 失效时 semantic memory 未能召回，"
        "说明检索路径耦合了向量搜索"
    )


def test_scenario2_exact_key_match_with_null_embedding(manager, db, user_id):
    """审查要求 4：exact key match 在 embedding 字段为 NULL 时依然能命中。

    semantic 检索对 embedding 是"并且"关系，不是"依赖"关系 ——
    字面匹配这条路不碰向量字段，任何情况下都执行。
    """
    _run(
        _seed(
            manager,
            [
                {
                    "action": "UPSERT",
                    "type": "semantic",
                    "key": "employment_status",
                    "value": "已从字节离职，目前自由职业",
                    "importance": 0.8,
                    "confidence": 0.95,
                }
            ],
        )
    )
    # 直接把整条记录的向量清空（NULL），模拟 embedding 从未生成
    _corrupt_embedding(db, user_id, "employment_status")

    # query 用 key 的精确拼写，走 exact_key_match
    results = _run(manager.retrieve("employment_status", top_k=5))

    matched = [m for m in results if getattr(m, "key", None) == "employment_status"]
    assert len(matched) == 1, (
        "embedding 为 NULL 时 exact key match 未能命中。"
        "如果这里失败，说明字面匹配这条路径依赖了向量或已被删除。"
    )


def test_scenario2_semantic_survives_embedding_disabled(manager, db, user_id, monkeypatch):
    """全局关掉 embedding（memory_embedding_enabled=False）时，
    semantic 记忆仍应能靠字面匹配召回 —— 这就是"不强制 embedding"。"""
    monkeypatch.setattr(settings, "memory_embedding_enabled", False)

    # embedding 被禁用，apply_actions 不会给任何行写向量
    _run(
        _seed(
            manager,
            [
                {
                    "action": "UPSERT",
                    "type": "semantic",
                    "key": "preferred_stack",
                    "value": "用 Python + FastAPI 做后端",
                    "importance": 0.8,
                    "confidence": 0.9,
                }
            ],
        )
    )

    # query 带上 value 的关键词
    results = _run(manager.retrieve("FastAPI 后端栈", top_k=5))

    matched = [m for m in results if getattr(m, "key", None) == "preferred_stack"]
    assert matched, (
        "memory_embedding_enabled=False 时 semantic 未能召回。"
        "semantic 的兜底是字面匹配，不该被 embedding 开关牵连。"
    )


def test_scenario3_high_importance_recalled_despite_low_similarity(manager, user_id):
    """早期存入一条与 query 语义都不相似、但 importance 很高的偏好记忆，
    验证它不会被相似度阈值直接过滤掉。"""
    _run(
        _seed(
            manager,
            [
                {
                    "action": "CREATE",
                    "type": "episodic",
                    "summary": "用户明确表示不喜欢代码中出现魔法数字，认为应该用命名常量",
                    "event_type": "preference",
                    "importance": 0.9,
                    "confidence": 0.9,
                }
            ],
        )
    )

    query = "帮我 review 这段代码：if retries > 3: raise TimeoutError()"

    results = _run(manager.retrieve(query, top_k=5))

    matched = [m for m in results if "魔法数字" in m.summary]
    assert matched, (
        "高重要性但低语义相似度的偏好记忆未被召回。"
        "如果这里失败，说明 retrieval 退化成了带阈值的纯向量检索，"
        "importance 加权没有真正生效。"
    )


def test_scenario4_upsert_overwrites_contradicting_fact(manager, user_id):
    """同一 key 的矛盾信息必须覆盖，不能并存。"""
    _run(
        _seed(
            manager,
            [
                {
                    "action": "UPSERT",
                    "type": "semantic",
                    "key": "employment_status",
                    "value": "在字节跳动工作",
                    "importance": 0.7,
                    "confidence": 0.9,
                }
            ],
        )
    )
    _run(
        _seed(
            manager,
            [
                {
                    "action": "UPSERT",
                    "type": "semantic",
                    "key": "employment_status",
                    "value": "已从字节离职，目前自由职业",
                    "importance": 0.7,
                    "confidence": 0.9,
                }
            ],
        )
    )

    results = _run(manager.retrieve("我现在在哪工作", top_k=5))

    matches = [m for m in results if getattr(m, "key", None) == "employment_status"]
    assert len(matches) == 1, (
        f"预期 UPSERT 后同一 key 只应存在一条最新记录，实际检索到 {len(matches)} 条，"
        f"说明矛盾信息没有被覆盖，而是并存了: {[m.value for m in matches]}"
    )
    assert "离职" in matches[0].value or "自由职业" in matches[0].value


def test_scenario5_importance_outranks_similarity_tie(manager, user_id):
    """两条对 query 相似度接近的记忆，importance 更高的应排前面。"""
    _run(
        _seed(
            manager,
            [
                {
                    "action": "CREATE",
                    "type": "episodic",
                    "summary": "用户今天喝了咖啡，心情不错",
                    "event_type": "trivial",
                    "importance": 0.1,
                    "confidence": 0.5,
                },
                {
                    "action": "CREATE",
                    "type": "episodic",
                    "summary": "用户决定将 MCP 作为 Agent 系统集成第三方工具的核心协议",
                    "event_type": "decision",
                    "importance": 0.95,
                    "confidence": 0.95,
                },
            ],
        )
    )

    results = _run(manager.retrieve("最近有什么值得关注的事情", top_k=5))

    assert results, "未召回任何记忆"
    top = results[0]
    assert "MCP" in top.summary, (
        f"预期 importance 更高的记忆排在首位，实际首位是: {top.summary!r}。"
        "检查 SCORE_WEIGHTS 里 importance 的权重是否生效，"
        "或是否退化成了纯相似度排序。"
    )


def test_scenario6_recency_does_not_override_high_importance_old_memory(
    manager, db, user_id
):
    """90 天前的高重要目标记忆，不能被刚发生的琐碎记忆靠 recency 压过去。"""
    seeded = _run(
        _seed(
            manager,
            [
                {
                    "action": "CREATE",
                    "type": "episodic",
                    "summary": "用户的核心目标是转型为 AI Agent 开发方向",
                    "event_type": "goal",
                    "importance": 0.95,
                    "confidence": 0.95,
                }
            ],
        )
    )
    _backdate(db, seeded[0].id, days=90)

    _run(
        _seed(
            manager,
            [
                {
                    "action": "CREATE",
                    "type": "episodic",
                    "summary": "用户今天心情不错",
                    "event_type": "trivial",
                    "importance": 0.1,
                    "confidence": 0.5,
                }
            ],
        )
    )

    results = _run(manager.retrieve("我的职业方向是什么", top_k=5))

    assert results, "未召回任何记忆"
    top = results[0]
    assert "AI Agent" in top.summary or "转型" in top.summary, (
        f"预期高 importance 的旧记忆排在首位，实际首位是: {top.summary!r}。"
        "如果最近但低重要性的记忆反而排前面，说明 recency 权重过高，"
        "导致'越新越重要'的误判。"
    )
