import json
import time
from functools import lru_cache
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .config import settings


REDIS_URL = settings.redis_url
_MEMORY_STORE: dict[str, tuple[str, float | None]] = {}
_REDIS_RETRY_AT = 0.0
_REDIS_RETRY_INTERVAL_SECONDS = 30


class RedisUnavailableError(RuntimeError):
    pass


def _memory_prune(key: str):
    value = _MEMORY_STORE.get(key)
    if value is None:
        return

    _, expires_at = value
    if expires_at is not None and expires_at <= time.time():
        _MEMORY_STORE.pop(key, None)


def _memory_set(key: str, value: str, ttl: int | None = None):
    expires_at = time.time() + ttl if ttl else None
    _MEMORY_STORE[key] = (value, expires_at)
    return True


def _memory_get(key: str) -> str | None:
    _memory_prune(key)
    value = _MEMORY_STORE.get(key)
    if value is None:
        return None
    return value[0]


def _memory_delete(*keys: str) -> int:
    deleted = 0
    for key in keys:
        _memory_prune(key)
        if key in _MEMORY_STORE:
            deleted += 1
            _MEMORY_STORE.pop(key, None)
    return deleted


def _memory_incr(key: str) -> int:
    current = _memory_get(key)
    count = int(current or "0") + 1
    _, expires_at = _MEMORY_STORE.get(key, ("", None))
    _MEMORY_STORE[key] = (str(count), expires_at)
    return count


def _memory_expire(key: str, ttl: int) -> bool:
    _memory_prune(key)
    value = _MEMORY_STORE.get(key)
    if value is None:
        return False
    _MEMORY_STORE[key] = (value[0], time.time() + ttl)
    return True


def _should_try_redis() -> bool:
    return time.time() >= _REDIS_RETRY_AT


def _mark_redis_unavailable():
    global _REDIS_RETRY_AT
    _REDIS_RETRY_AT = time.time() + _REDIS_RETRY_INTERVAL_SECONDS


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def redis_set(key: str, value: str, ttl: int | None = None):
    if not _should_try_redis():
        return _memory_set(key, value, ttl=ttl)

    try:
        return get_redis().set(key, value, ex=ttl)
    except RedisError:
        _mark_redis_unavailable()
        return _memory_set(key, value, ttl=ttl)


def redis_get(key: str) -> str | None:
    if not _should_try_redis():
        return _memory_get(key)

    try:
        return get_redis().get(key)
    except RedisError:
        _mark_redis_unavailable()
        return _memory_get(key)


def redis_delete(*keys: str) -> int:
    if not keys:
        return 0

    if not _should_try_redis():
        return _memory_delete(*keys)

    try:
        return int(get_redis().delete(*keys))
    except RedisError:
        _mark_redis_unavailable()
        return _memory_delete(*keys)


def redis_incr(key: str) -> int | None:
    if not _should_try_redis():
        return _memory_incr(key)

    try:
        return get_redis().incr(key)
    except RedisError:
        _mark_redis_unavailable()
        return _memory_incr(key)


def redis_expire(key: str, ttl: int) -> bool:
    if not _should_try_redis():
        return _memory_expire(key, ttl)

    try:
        return bool(get_redis().expire(key, ttl))
    except RedisError:
        _mark_redis_unavailable()
        return _memory_expire(key, ttl)
    
def redis_set_json(key: str, value: Any, ttl: int | None = None):
    return redis_set(key, json.dumps(value, ensure_ascii=False), ttl=ttl)


def redis_get_json(key: str) -> Any | None:
    value = redis_get(key)
    if value is None:
        return None

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        redis_delete(key)
        return None
