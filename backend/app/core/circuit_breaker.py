"""LLM 上游熔断器。

为什么要它
----------
阶段 1 的重试是"每个请求各自硬扛"：上游连续故障时，每个请求仍会重试 3 次，
反而把压力放大。熔断在连续失败后整体停手一段时间，给上游恢复窗口，也让
用户拿到明确的错误，而不是继续等下一轮超时。

状态机（粒度：provider + base_url，同一家的不同模型通常共用出口）::

    CLOSED    正常放行
    OPEN      冷却期内直接拒绝，不发起任何请求
    HALF_OPEN 冷却结束，只放一个探测请求；成功回 CLOSED，失败重新 OPEN

存储用 Redis（与限流、停止信号一致，多进程共享）；Redis 不可用时退化为
进程内 dict，单进程部署语义不变。状态更新是读-改-写、非严格原子，多副本
下允许近似——目标是不再把流量持续打向已经挂掉的上游，不是分布式锁。
"""

import time
from urllib.parse import urlparse

from . import redis
from .config import settings

# 熔断中：调用方看到它说明"根本没发请求"，与上游返回的错误区别开
class CircuitOpenError(RuntimeError):
    pass


def circuit_key(provider: str, base_url: str) -> str:
    """按 provider + 出口地址聚合，忽略 path/query 的差异。"""
    host = urlparse(base_url).netloc or base_url
    return f"llm:circuit:{provider}:{host}"


def _probe_key(key: str) -> str:
    return f"{key}:probe"


def _state_ttl() -> int:
    # 冷却结束后状态就该失效，留一倍余量给 HALF_OPEN 探测
    return settings.llm_circuit_cooldown_seconds * 2 + settings.llm_circuit_probe_ttl_seconds


def _load(key: str) -> dict:
    return redis.redis_get_json(key) or {"failures": 0, "opened_at": None}


def acquire(provider: str, base_url: str) -> bool:
    """发起请求前调用。False 表示当前熔断打开，不要发请求。"""
    key = circuit_key(provider, base_url)
    state = _load(key)
    opened_at = state.get("opened_at")

    if opened_at is None:
        return True

    if time.time() < opened_at + settings.llm_circuit_cooldown_seconds:
        return False

    # HALF_OPEN：用一个带 TTL 的计数器限制"同时只放一个探测"
    probe_key = _probe_key(key)
    count = redis.redis_incr(probe_key)
    if count == 1:
        redis.redis_expire(probe_key, settings.llm_circuit_probe_ttl_seconds)
        return True
    return False


def record_success(provider: str, base_url: str) -> None:
    """成功即视为上游恢复：清空失败计数与打开时间。"""
    key = circuit_key(provider, base_url)
    redis.redis_delete(key, _probe_key(key))


def record_failure(provider: str, base_url: str) -> bool:
    """记一次可重试类失败，返回本次是否把熔断打开。"""
    key = circuit_key(provider, base_url)
    state = _load(key)
    failures = int(state.get("failures") or 0) + 1
    already_open = state.get("opened_at") is not None

    # 已经打开过（HALF_OPEN 探测失败）就重新计时，而不是再累计
    if already_open or failures >= settings.llm_circuit_failure_threshold:
        next_state = {"failures": failures, "opened_at": time.time()}
    else:
        next_state = {"failures": failures, "opened_at": None}

    redis.redis_set_json(key, next_state, ttl=_state_ttl())
    return next_state["opened_at"] is not None


def reset(provider: str, base_url: str) -> None:
    """仅供测试/运维手动恢复使用。"""
    key = circuit_key(provider, base_url)
    redis.redis_delete(key, _probe_key(key))
