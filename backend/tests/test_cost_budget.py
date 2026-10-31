"""成本账本与额度闸门测试。

覆盖迭代 2C 的验收项：
    - 价格表前缀最长匹配、金额折算正确
    - 未知模型不崩溃，cost_micros=0
    - 每次调用写一条账本，并累加 Redis 日计数
    - 用户日额度 / 全局日成本超限时拒绝；额度 0 不限制
    - Redis 不可用时计数退化为进程内

用法（在 backend 目录下运行）：
    python -m pytest tests/test_cost_budget.py -v
"""

import sys
from pathlib import Path

import pytest
from redis.exceptions import RedisError
from sqlalchemy import select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.core.redis as redis_mod  # noqa: E402
import app.usage.service as usage_service  # noqa: E402
from app.chat.schemas import TokenUsage  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.model_pricing import cost_micros, price_for  # noqa: E402
from app.usage.models import LlmUsageLog  # noqa: E402

# 供"Redis 不可用降级"用例临时换回真实实现
_REAL_REDIS_FUNCS = {
    name: getattr(redis_mod, name)
    for name in ("redis_delete", "redis_get", "redis_set", "redis_set_json", "redis_incrby", "redis_expire")
}

DAY = "20260101"


# ---------------------------------------------------------------------------
# 价格表
# ---------------------------------------------------------------------------


def test_price_prefix_longest_match():
    assert price_for("deepseek-v4-flash").provider == "deepseek"
    assert price_for("gpt-4o-mini").output_micros_per_1k == 4300
    # gpt-4o-mini 必须命中自己的规则，而不是更短的 gpt-4o
    assert price_for("gpt-4o").output_micros_per_1k == 72000


def test_unknown_model_has_no_price_and_zero_cost():
    assert price_for("mystery-model") is None
    assert price_for(None) is None
    assert cost_micros("unknown", "mystery-model", 1000, 1000) == 0


def test_cost_micros_matches_price_table():
    # deepseek-chat: 输入 2000 微元/1K，输出 8000 微元/1K
    assert cost_micros("deepseek", "deepseek-chat", 1000, 500) == 2000 + 4000
    assert cost_micros("deepseek", "deepseek-chat", 0, 0) == 0


# ---------------------------------------------------------------------------
# 记账
# ---------------------------------------------------------------------------


def test_record_usage_writes_ledger_and_counters(db, user_id, monkeypatch):
    monkeypatch.setattr(usage_service, "_today", lambda: DAY)
    usage = TokenUsage(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
    )

    log = usage_service.record_llm_usage(
        db,
        user_id=user_id,
        session_id=None,
        scene="chat",
        usage=usage,
        latency_ms=123,
    )

    assert log.cost_micros == 6000
    assert log.total_tokens == 1500
    assert log.provider == "deepseek"
    assert log.model == "deepseek-chat"
    assert log.status == "success"

    row = db.execute(select(LlmUsageLog).where(LlmUsageLog.id == log.id)).scalar_one()
    assert row.cost_micros == 6000

    assert usage_service.user_token_used(user_id, DAY) == 1500
    assert usage_service.global_cost_used_micros(DAY) == 6000


def test_record_unknown_model_does_not_crash(db, user_id, monkeypatch):
    monkeypatch.setattr(usage_service, "_today", lambda: DAY)
    usage = TokenUsage(
        provider="deepseek",
        model="mystery-model",
        prompt_tokens=100,
        completion_tokens=100,
        total_tokens=200,
    )

    log = usage_service.record_llm_usage(db, user_id=user_id, usage=usage)

    assert log.cost_micros == 0
    assert log.total_tokens == 200


# ---------------------------------------------------------------------------
# 预算闸门
# ---------------------------------------------------------------------------


def test_user_budget_zero_means_unlimited(user_id, monkeypatch):
    monkeypatch.setattr(usage_service, "_today", lambda: DAY)
    monkeypatch.setattr(settings, "chat_user_daily_token_budget", 0)
    monkeypatch.setattr(settings, "global_daily_cost_limit_yuan", 0)
    redis_mod.redis_set(usage_service.user_token_key(user_id, DAY), "999999")

    assert usage_service.user_budget_exceeded(user_id, DAY) is False
    assert usage_service.budget_rejection_message(user_id, DAY) is None


def test_user_budget_exceeded_rejects(user_id, monkeypatch):
    monkeypatch.setattr(usage_service, "_today", lambda: DAY)
    monkeypatch.setattr(settings, "chat_user_daily_token_budget", 100)
    monkeypatch.setattr(settings, "global_daily_cost_limit_yuan", 0)

    redis_mod.redis_set(usage_service.user_token_key(user_id, DAY), "99")
    assert usage_service.budget_rejection_message(user_id, DAY) is None

    redis_mod.redis_set(usage_service.user_token_key(user_id, DAY), "100")
    assert (
        usage_service.budget_rejection_message(user_id, DAY)
        == usage_service.USER_BUDGET_MESSAGE
    )


def test_global_cost_limit_rejects(user_id, monkeypatch):
    monkeypatch.setattr(usage_service, "_today", lambda: DAY)
    monkeypatch.setattr(settings, "chat_user_daily_token_budget", 0)
    monkeypatch.setattr(settings, "global_daily_cost_limit_yuan", 0.01)  # 10000 微元

    redis_mod.redis_set(usage_service.global_cost_key(DAY), "10000")
    assert (
        usage_service.budget_rejection_message(user_id, DAY)
        == usage_service.GLOBAL_BUDGET_MESSAGE
    )


# ---------------------------------------------------------------------------
# Redis 降级
# ---------------------------------------------------------------------------


def test_counters_degrade_to_memory_when_redis_down(db, user_id, monkeypatch):
    def boom(*args, **kwargs):
        raise RedisError("redis down")

    monkeypatch.setattr(redis_mod, "get_redis", boom)
    monkeypatch.setattr(redis_mod, "_should_try_redis", lambda: True)
    for name, func in _REAL_REDIS_FUNCS.items():
        monkeypatch.setattr(redis_mod, name, func)

    day = "20991231"
    monkeypatch.setattr(usage_service, "_today", lambda: day)
    usage = TokenUsage(
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=1000,
        completion_tokens=0,
        total_tokens=1000,
    )

    log = usage_service.record_llm_usage(db, user_id=user_id, usage=usage)

    assert log.cost_micros == 2000
    assert usage_service.user_token_used(user_id, day) == 1000
    assert usage_service.global_cost_used_micros(day) == 2000


@pytest.mark.parametrize("scene", ["chat", "agent"])
def test_record_supports_scene(db, user_id, scene):
    log = usage_service.record_llm_usage(
        db,
        user_id=user_id,
        scene=scene,
        provider="deepseek",
        model="deepseek-chat",
        prompt_tokens=10,
        completion_tokens=10,
        latency_ms=5,
        status="success",
    )
    assert log.scene == scene
