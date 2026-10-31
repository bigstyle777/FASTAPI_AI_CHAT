import json
import time
from functools import lru_cache
from typing import Any, Callable

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


def _memory_incrby(key: str, amount: int) -> int:
    current = _memory_get(key)
    count = int(current or "0") + amount
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


def _call_redis(real: Callable[[], Any], fallback: Callable[[], Any]):
    """尝试 Redis 操作，失败降级到进程内内存副本。

    所有 redis_xxx 操作共用这一段：Redis 暂不可用时标记 30 秒内
    直接走 fallback，避免每个操作各自重复 try/except + 降级样板。
    """
    if not _should_try_redis():
        return fallback()
    try:
        return real()
    except RedisError:
        _mark_redis_unavailable()
        return fallback()


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def redis_set(key: str, value: str, ttl: int | None = None):
    return _call_redis(
        lambda: get_redis().set(key, value, ex=ttl),
        lambda: _memory_set(key, value, ttl=ttl),
    )


def redis_get(key: str) -> str | None:
    return _call_redis(
        lambda: get_redis().get(key),
        lambda: _memory_get(key),
    )


def redis_delete(*keys: str) -> int:
    if not keys:
        return 0
    return _call_redis(
        lambda: int(get_redis().delete(*keys)),
        lambda: _memory_delete(*keys),
    )


def redis_incr(key: str) -> int | None:
    return _call_redis(
        lambda: get_redis().incr(key),
        lambda: _memory_incrby(key, 1),
    )


def redis_expire(key: str, ttl: int) -> bool:
    return _call_redis(
        lambda: bool(get_redis().expire(key, ttl)),
        lambda: _memory_expire(key, ttl),
    )


def redis_incrby(key: str, amount: int) -> int:
    """原子累加（预算计数用）；amount 可为负数。"""
    return _call_redis(
        lambda: int(get_redis().incrby(key, amount)),
        lambda: _memory_incrby(key, amount),
    )


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
