"""熔断器状态机测试（不依赖真实 LLM / Redis）。

覆盖迭代 2A 的验收项：
    - 连续失败达阈值才打开，打开后不再发起请求
    - 只统计可重试类错误（401/400 不打熔断）
    - 冷却后只放一个探测；探测成功回 CLOSED，失败重新 OPEN
    - Redis 不可用时退化为进程内状态

conftest 的 autouse fixture 已把 app.core.redis 换成内存 dict，
所以这里能直接观察键值、也不需要真实 Redis。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_circuit_breaker.py -v
"""

import asyncio
import sys
import time
from pathlib import Path

import httpx
import pytest
from openai import AuthenticationError, BadRequestError, InternalServerError
from redis.exceptions import RedisError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.core.circuit_breaker as circuit_breaker  # noqa: E402
import app.core.redis as redis_mod  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.retry import call_with_retry  # noqa: E402

# 模块导入时把真实实现存下来：conftest 会在用例运行时换成 mock，
# "Redis 不可用退化" 这条用例需要临时换回真实实现才能验证降级路径。
_REAL_REDIS_FUNCS = {
    name: getattr(redis_mod, name)
    for name in (
        "redis_delete",
        "redis_get",
        "redis_get_json",
        "redis_incr",
        "redis_expire",
        "redis_set",
        "redis_set_json",
    )
}

BASE = "https://api.test/v1"


def _request() -> httpx.Request:
    return httpx.Request("POST", f"{BASE}/chat/completions")


def _status_error(cls, status: int):
    return cls("boom", response=httpx.Response(status, request=_request()), body=None)


@pytest.fixture
def fast_retry(monkeypatch):
    """不真等退避，测试只关心"还发不发请求"。"""
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return delays


async def _always_fail():
    raise _status_error(InternalServerError, 500)


# ---------------------------------------------------------------------------
# 状态流转
# ---------------------------------------------------------------------------


def test_circuit_key_ignores_path():
    assert (
        circuit_breaker.circuit_key("openai", "https://api.openai.com/v1")
        == "llm:circuit:openai:api.openai.com"
    )


def test_failures_below_threshold_stay_closed():
    threshold = settings.llm_circuit_failure_threshold
    for _ in range(threshold - 1):
        assert circuit_breaker.record_failure("deepseek", BASE) is False
    assert circuit_breaker.acquire("deepseek", BASE) is True


def test_threshold_opens_circuit():
    threshold = settings.llm_circuit_failure_threshold
    opened = False
    for _ in range(threshold):
        opened = circuit_breaker.record_failure("deepseek", BASE)
    assert opened is True
    assert circuit_breaker.acquire("deepseek", BASE) is False


def test_open_circuit_blocks_request_without_calling_upstream(fast_retry):
    calls = 0

    async def counted():
        nonlocal calls
        calls += 1
        raise _status_error(InternalServerError, 500)

    threshold = settings.llm_circuit_failure_threshold
    for _ in range(threshold):
        with pytest.raises(InternalServerError):
            asyncio.run(
                call_with_retry(
                    counted,
                    max_attempts=1,
                    base_seconds=0.5,
                    cap_seconds=4.0,
                    circuit_target=("deepseek", BASE),
                )
            )
    assert calls == threshold

    # 熔断已打开：这一次连 fn 都不该执行
    with pytest.raises(circuit_breaker.CircuitOpenError):
        asyncio.run(
            call_with_retry(
                counted,
                max_attempts=1,
                base_seconds=0.5,
                cap_seconds=4.0,
                circuit_target=("deepseek", BASE),
            )
        )
    assert calls == threshold


def test_non_retryable_errors_do_not_open_circuit(fast_retry):
    async def unauthorized():
        raise _status_error(AuthenticationError, 401)

    async def bad_request():
        raise _status_error(BadRequestError, 400)

    for error_fn in (unauthorized, bad_request):
        for _ in range(10):
            with pytest.raises((AuthenticationError, BadRequestError)):
                asyncio.run(
                    call_with_retry(
                        error_fn,
                        max_attempts=3,
                        base_seconds=0.5,
                        cap_seconds=4.0,
                        circuit_target=("deepseek", BASE),
                    )
                )

    assert circuit_breaker.acquire("deepseek", BASE) is True
    assert redis_mod.redis_get_json(circuit_breaker.circuit_key("deepseek", BASE)) is None


def _open_circuit_in_past():
    key = circuit_breaker.circuit_key("deepseek", BASE)
    redis_mod.redis_set_json(
        key,
        {
            "failures": settings.llm_circuit_failure_threshold,
            "opened_at": time.time() - settings.llm_circuit_cooldown_seconds - 1,
        },
    )
    return key


def test_half_open_allows_single_probe_then_success_closes():
    _open_circuit_in_past()

    assert circuit_breaker.acquire("deepseek", BASE) is True  # 探测名额
    assert circuit_breaker.acquire("deepseek", BASE) is False  # 已有探测在跑

    circuit_breaker.record_success("deepseek", BASE)
    assert circuit_breaker.acquire("deepseek", BASE) is True
    assert redis_mod.redis_get_json(circuit_breaker.circuit_key("deepseek", BASE)) is None


def test_half_open_probe_failure_reopens():
    _open_circuit_in_past()

    assert circuit_breaker.acquire("deepseek", BASE) is True
    assert circuit_breaker.record_failure("deepseek", BASE) is True
    # 重新计时，冷却期内继续拒绝
    assert circuit_breaker.acquire("deepseek", BASE) is False


def test_circuit_is_per_provider_and_host():
    circuit_breaker.record_failure("deepseek", BASE)
    assert circuit_breaker.acquire("openai", BASE) is True


# ---------------------------------------------------------------------------
# Redis 不可用 → 进程内状态
# ---------------------------------------------------------------------------


def test_state_degrades_to_memory_when_redis_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise RedisError("redis down")

    monkeypatch.setattr(redis_mod, "get_redis", boom)
    monkeypatch.setattr(redis_mod, "_should_try_redis", lambda: True)
    for name, func in _REAL_REDIS_FUNCS.items():
        monkeypatch.setattr(redis_mod, name, func)

    base = "https://memory-degrade.test/v1"
    opened = False
    for _ in range(settings.llm_circuit_failure_threshold):
        opened = circuit_breaker.record_failure("deepseek", base)
    assert opened is True
    assert circuit_breaker.acquire("deepseek", base) is False

    circuit_breaker.record_success("deepseek", base)
    assert circuit_breaker.acquire("deepseek", base) is True
