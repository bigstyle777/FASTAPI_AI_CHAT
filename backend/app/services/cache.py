from ..core.config import settings
from ..core.redis import (
    redis_delete,
    redis_expire,
    redis_get_json,
    redis_incr,
    redis_set_json,
)


def _chat_context_key(session_id: int):
    return f"chat_context:{session_id}"


def get_chat_context(session_id: int):
    return redis_get_json(_chat_context_key(session_id))


def set_chat_context(session_id: int, messages: list):
    return redis_set_json(
        _chat_context_key(session_id), messages, ttl=settings.chat_ttl_seconds
    )


def invalidate_chat_cache(session_id: int):
    return redis_delete(_chat_context_key(session_id))


def delete_chat_context(session_id: int):
    return redis_delete(_chat_context_key(session_id))


def check_rate_limit(key: str, limit: int, expire_seconds: int):
    count = redis_incr(key)
    if count == 1:
        redis_expire(key, expire_seconds)
    return count <= limit  # type: ignore # ty:ignore[unsupported-operator]
