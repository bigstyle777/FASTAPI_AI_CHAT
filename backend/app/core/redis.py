import json
from functools import lru_cache
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from .config import settings


REDIS_URL = settings.redis_url


class RedisUnavailableError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def get_redis() -> Redis:
    return Redis.from_url(
        REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def redis_set(key: str, value: str, ttl: int | None = None):
    try:
        return get_redis().set(key, value, ex=ttl)
    except RedisError as error:
        raise RedisUnavailableError("Redis is unavailable") from error


def redis_get(key: str) -> str | None:
    try:
        return get_redis().get(key)
    except RedisError as error:
        raise RedisUnavailableError("Redis is unavailable") from error


def redis_delete(*keys: str) -> int:
    if not keys:
        return 0

    try:
        return int(get_redis().delete(*keys))
    except RedisError as error:
        raise RedisUnavailableError("Redis is unavailable") from error


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
