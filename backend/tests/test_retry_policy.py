"""core/retry.py 的错误分类与重试策略测试。

不依赖真实 LLM：用 openai 真实异常类构造故障，验证分类、重试次数、
退避序列。monkeypatch 掉 asyncio.sleep 和 random.uniform，测试不真等、
结果确定性。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_retry_policy.py -v
"""

import asyncio
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.retry import (  # noqa: E402
    call_with_retry,
    create_chat_completion_with_retry,
    is_retryable_llm_error,
)


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.test/v1/chat/completions")


def _status_error(cls, status: int):
    return cls("boom", response=httpx.Response(status, request=_request()), body=None)


# ---------------------------------------------------------------------------
# 错误分类
# ---------------------------------------------------------------------------


def test_retryable_errors():
    assert is_retryable_llm_error(APIConnectionError(request=_request()))
    assert is_retryable_llm_error(APITimeoutError(_request()))
    assert is_retryable_llm_error(_status_error(RateLimitError, 429))
    assert is_retryable_llm_error(_status_error(InternalServerError, 500))


def test_non_retryable_errors():
    assert not is_retryable_llm_error(_status_error(AuthenticationError, 401))
    assert not is_retryable_llm_error(_status_error(BadRequestError, 400))
    assert not is_retryable_llm_error(ValueError("unknown"))


# ---------------------------------------------------------------------------
# 重试行为
# ---------------------------------------------------------------------------


@pytest.fixture
def fast_retry(monkeypatch):
    """把 sleep 和抖动换成确定性的：记录退避序列，测试不真等。"""
    delays = []

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    # 全抖动取上界，delay = min(cap, base * 2^attempt)，结果确定性
    monkeypatch.setattr(random, "uniform", lambda a, b: b)
    return delays


def test_retry_succeeds_after_transient_failures(fast_retry):
    attempts = 0

    async def flaky():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise APIConnectionError(request=_request())
        return "ok"

    result = asyncio.run(
        call_with_retry(flaky, max_attempts=3, base_seconds=0.5, cap_seconds=4.0)
    )

    assert result == "ok"
    assert attempts == 3
    assert fast_retry == [0.5, 1.0]  # 指数递增


def test_retry_gives_up_after_max_attempts(fast_retry):
    attempts = 0

    async def always_fail():
        nonlocal attempts
        attempts += 1
        raise _status_error(InternalServerError, 500)

    with pytest.raises(InternalServerError):
        asyncio.run(
            call_with_retry(always_fail, max_attempts=3, base_seconds=0.5, cap_seconds=4.0)
        )

    assert attempts == 3
    assert fast_retry == [0.5, 1.0]  # 最后一次失败不再退避


def test_non_retryable_error_fails_fast(fast_retry):
    attempts = 0

    async def bad_request():
        nonlocal attempts
        attempts += 1
        raise _status_error(AuthenticationError, 401)

    with pytest.raises(AuthenticationError):
        asyncio.run(
            call_with_retry(bad_request, max_attempts=3, base_seconds=0.5, cap_seconds=4.0)
        )

    assert attempts == 1
    assert fast_retry == []


def test_backoff_respects_cap(fast_retry):
    async def always_fail():
        raise APIConnectionError(request=_request())

    with pytest.raises(APIConnectionError):
        asyncio.run(
            call_with_retry(always_fail, max_attempts=6, base_seconds=0.5, cap_seconds=4.0)
        )

    assert fast_retry == [0.5, 1.0, 2.0, 4.0, 4.0]  # 到 cap 后不再翻倍


# ---------------------------------------------------------------------------
# create_chat_completion_with_retry
# ---------------------------------------------------------------------------


class _FakeCompletions:
    """假 completions：按预置效果依次抛错或返回。"""

    def __init__(self, effects):
        self._effects = list(effects)
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        effect = self._effects.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect


def test_create_chat_completion_retries_then_returns(fast_retry):
    completions = _FakeCompletions(
        [_status_error(RateLimitError, 429), SimpleNamespace(choices=[])]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = asyncio.run(
        create_chat_completion_with_retry(client, model="m", messages=[])
    )

    assert result.choices == []
    assert completions.calls == 2


def test_create_chat_completion_non_retryable_not_retried(fast_retry):
    completions = _FakeCompletions([_status_error(BadRequestError, 400)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    with pytest.raises(BadRequestError):
        asyncio.run(create_chat_completion_with_retry(client, model="m", messages=[]))

    assert completions.calls == 1
