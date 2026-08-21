from ..core import redis
from ..core.config import settings


# 上下文缓存
def _chat_context_key(session_id: int):
    return f"chat_context:{session_id}"


def get_chat_context(session_id: int):
    return redis.redis_get_json(_chat_context_key(session_id))


def set_chat_context(session_id: int, messages: list):
    return redis.redis_set_json(
        _chat_context_key(session_id), messages, ttl=settings.chat_ttl_seconds
    )


def invalidate_chat_cache(session_id: int):
    return redis.redis_delete(_chat_context_key(session_id))


def delete_chat_context(session_id: int):
    return redis.redis_delete(_chat_context_key(session_id))


def check_rate_limit(key: str, limit: int, expire_seconds: int):
    count = redis.redis_incr(key)
    if count == 1:
        redis.redis_expire(key, expire_seconds)
    return count <= limit  # type: ignore # ty:ignore[unsupported-operator]


# 输出控制
def set_generation_status(session_id: int, status: str):
    redis.redis_set(
        f"chat:generation:{session_id}",
        status,
        ttl=settings.stop_generation_ttl_seconds,
    )


def get_generation_status(session_id: int):
    return redis.redis_get(f"chat:generation:{session_id}")


def is_stop_requested(session_id: int):
    status = get_generation_status(session_id)
    return status == "stop_requested"


def clear_generation_status(session_id: int):
    redis.redis_delete(f"chat:generation:{session_id}")
