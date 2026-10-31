"""模型调用成本账本与额度闸门。

分工：

- **PG 账本**（``llm_usage_logs``）是权威：一行一次调用，金额按价目表折算。
- **Redis 日计数**只是事前闸门：请求前读一次，判断"还能不能发"。
- 顺序是"先落库、再累加计数"：计数偏少最多多放行一个请求，不会少扣。

默认额度为 0 = 不限制，现网行为不变；只有显式配置后才生效。
日界按服务器本地日期（0 点重置），键自然过期，不需要清理任务。
"""

import logging
from datetime import datetime

from ..core import redis
from ..core.config import settings
from ..core.model_pricing import cost_micros
from .models import LlmUsageLog

logger = logging.getLogger(__name__)

SCENE_CHAT = "chat"
SCENE_AGENT = "agent"

# 日计数保留两天：跨过 0 点后旧键自然过期，不额外跑清理任务
_COUNTER_TTL_SECONDS = 2 * 24 * 3600

USER_BUDGET_MESSAGE = "今日 Token 额度已用完，明日 0 点重置"
GLOBAL_BUDGET_MESSAGE = "今日系统额度已用完，请明日再试"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def user_token_key(user_id: int, day: str | None = None) -> str:
    return f"budget:user:{user_id}:{day or _today()}"


def global_cost_key(day: str | None = None) -> str:
    return f"budget:global:{day or _today()}"


def record_llm_usage(
    db,
    *,
    user_id: int,
    session_id: int | None = None,
    scene: str = SCENE_CHAT,
    provider: str | None = "",
    model: str | None = None,
    usage=None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int | None = None,
    status: str = "success",
    latency_ms: int = 0,
    error: str | None = None,
) -> LlmUsageLog:
    """写一条账本，并累加当日 Redis 计数（先 PG、后 Redis）。"""
    if usage is not None:
        prompt_tokens = int(getattr(usage, "prompt_tokens", prompt_tokens) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", completion_tokens) or 0)
        model = model or getattr(usage, "model", None)
        provider = provider or getattr(usage, "provider", None) or ""

    total = prompt_tokens + completion_tokens if total_tokens is None else total_tokens
    cost = cost_micros(provider, model, prompt_tokens, completion_tokens)

    log = LlmUsageLog(
        user_id=user_id,
        session_id=session_id,
        scene=scene,
        provider=provider or "",
        model=model,
        status=status,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total,
        cost_micros=cost,
        latency_ms=max(0, int(latency_ms)),
        error=error,
    )
    try:
        db.add(log)
        db.commit()
        db.refresh(log)
    except Exception:
        db.rollback()
        raise

    day = _today()
    key = user_token_key(user_id, day)
    redis.redis_incrby(key, total)
    redis.redis_expire(key, _COUNTER_TTL_SECONDS)
    if cost:
        gkey = global_cost_key(day)
        redis.redis_incrby(gkey, cost)
        redis.redis_expire(gkey, _COUNTER_TTL_SECONDS)
    return log


def user_token_used(user_id: int, day: str | None = None) -> int:
    return int(redis.redis_get(user_token_key(user_id, day)) or 0)


def global_cost_used_micros(day: str | None = None) -> int:
    return int(redis.redis_get(global_cost_key(day)) or 0)


def user_budget_exceeded(user_id: int, day: str | None = None) -> bool:
    budget = settings.chat_user_daily_token_budget
    if budget <= 0:
        return False
    return user_token_used(user_id, day) >= budget


def global_budget_exceeded(day: str | None = None) -> bool:
    limit_yuan = settings.global_daily_cost_limit_yuan
    if limit_yuan <= 0:
        return False
    limit_micros = int(round(limit_yuan * 1_000_000))
    return global_cost_used_micros(day) >= limit_micros


def budget_rejection_message(user_id: int, day: str | None = None) -> str | None:
    """生成前闸门：超限返回给用户看的文案，未超限返回 None。"""
    if user_budget_exceeded(user_id, day):
        return USER_BUDGET_MESSAGE
    if global_budget_exceeded(day):
        return GLOBAL_BUDGET_MESSAGE
    return None


def safe_record(*args, **kwargs) -> LlmUsageLog | None:
    """记账失败的统一兜底：账本/计数异常不能把已经生成的回答变成报错。"""
    try:
        return record_llm_usage(*args, **kwargs)
    except Exception:  # noqa: BLE001
        logger.exception("LLM 用量记账失败")
        return None
