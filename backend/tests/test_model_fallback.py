"""备用模型降级链测试（不依赖真实 LLM）。

覆盖迭代 2B 的验收项：
    - 目标列表解析：用户 Key 只换模型不换供应商；系统 Key 可跨供应商
    - 主目标失败（重试耗尽）→ 切备用目标并成功出字
    - 已吐出首个 token 后失败 → 不切换，直接报错
    - 401 不触发切换

用 ``asyncio.run()`` 包裹协程，沿用项目现有测试风格（不引入 pytest-asyncio）。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_model_fallback.py -v
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, AuthenticationError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.chat.services.llm as llm  # noqa: E402
import app.core.ai_client as ai_client  # noqa: E402
from app.chat.schemas import StreamDeltaEvent, StreamUsageEvent, TokenUsage  # noqa: E402
from app.core.ai_client import ModelTarget, get_client, resolve_model_targets  # noqa: E402

BASE = "https://api.test/v1"


def _request() -> httpx.Request:
    return httpx.Request("POST", f"{BASE}/chat/completions")


def _status_error(cls, status: int):
    return cls("boom", response=httpx.Response(status, request=_request()), body=None)


def _target(provider, model, *, is_user_key=False):
    return ModelTarget(provider, BASE, model, "key", is_user_key)


def _run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    return [event async for event in agen]


# ---------------------------------------------------------------------------
# 目标列表解析
# ---------------------------------------------------------------------------


def test_user_key_fallback_never_crosses_provider(monkeypatch):
    monkeypatch.setattr(ai_client.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(ai_client.settings, "llm_fallback_model", "deepseek-chat")
    monkeypatch.setattr(ai_client.settings, "llm_fallback_provider", None)
    monkeypatch.setattr(ai_client.settings, "deepseek_model", "deepseek-v4-flash")

    targets = resolve_model_targets("user-key", "deepseek")

    assert [t.model for t in targets] == ["deepseek-v4-flash", "deepseek-chat"]
    assert {t.provider for t in targets} == {"deepseek"}
    assert all(t.is_user_key for t in targets)


def test_system_key_can_cross_provider(monkeypatch):
    monkeypatch.setattr(ai_client.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(ai_client.settings, "llm_fallback_provider", None)
    monkeypatch.setattr(ai_client.settings, "llm_fallback_model", None)
    monkeypatch.setattr(ai_client.settings, "deepseek_api_key", "system-deepseek")
    monkeypatch.setattr(ai_client.settings, "openai_api_key", "system-openai")
    monkeypatch.setattr(ai_client.settings, "deepseek_model", "deepseek-chat")
    monkeypatch.setattr(ai_client.settings, "openai_model", "gpt-4o-mini")
    # 系统 Key 回落是显式开启的部署选项，默认关闭
    monkeypatch.setattr(ai_client.settings, "server_api_key_fallback_enabled", True)

    targets = resolve_model_targets(None, "deepseek")

    assert [t.provider for t in targets] == ["deepseek", "openai"]
    assert [t.model for t in targets] == ["deepseek-chat", "gpt-4o-mini"]
    assert all(not t.is_user_key for t in targets)


def test_fallback_disabled_returns_single_target(monkeypatch):
    monkeypatch.setattr(ai_client.settings, "llm_fallback_enabled", False)
    monkeypatch.setattr(ai_client.settings, "deepseek_api_key", "system-deepseek")
    monkeypatch.setattr(ai_client.settings, "server_api_key_fallback_enabled", True)

    assert len(resolve_model_targets(None, "deepseek")) == 1


def test_no_server_key_fallback_by_default(monkeypatch):
    """安全默认：未显式开启回落时，服务器全局 Key 不可见。"""
    monkeypatch.setattr(ai_client.settings, "llm_fallback_enabled", True)
    monkeypatch.setattr(ai_client.settings, "deepseek_api_key", "system-deepseek")
    monkeypatch.setattr(ai_client.settings, "server_api_key_fallback_enabled", False)

    assert resolve_model_targets(None, "deepseek") == []
    assert get_client(None) is None


def test_no_key_returns_empty_targets(monkeypatch):
    monkeypatch.setattr(ai_client.settings, "deepseek_api_key", None)
    monkeypatch.setattr(ai_client.settings, "openai_api_key", None)

    assert resolve_model_targets(None, "deepseek") == []


# ---------------------------------------------------------------------------
# 流式降级
# ---------------------------------------------------------------------------


def _stream_raiser(called, name, error):
    async def gen(client, model, messages, **kwargs):
        called.append(model)
        raise error
        yield  # pragma: no cover - 让函数成为异步生成器

    return gen


def _stream_events(called, name, events, error_after=None):
    async def gen(client, model, messages, **kwargs):
        called.append(model)
        for event in events:
            yield event
        if error_after is not None:
            raise error_after

    return gen


def _patch_streaming(monkeypatch, targets, behaviours):
    monkeypatch.setattr(llm, "resolve_model_targets", lambda api_key, provider: targets)
    monkeypatch.setattr(llm, "get_async_client_for", lambda target: SimpleNamespace(model=target.model))
    monkeypatch.setattr(
        llm,
        "stream_with_tools",
        lambda client, model, messages, **kwargs: behaviours[model](
            client, model, messages, **kwargs
        ),
    )

    async def fake_run_blocking(func, *args, **kwargs):
        return (None, "deepseek")

    monkeypatch.setattr(llm, "run_blocking", fake_run_blocking)


def test_stream_falls_back_before_first_token(monkeypatch):
    targets = [_target("deepseek", "main"), _target("openai", "backup")]
    called = []
    behaviours = {
        "main": _stream_raiser(called, "main", APIConnectionError(request=_request())),
        "backup": _stream_events(called, "backup", [StreamDeltaEvent(content="你好")]),
    }
    _patch_streaming(monkeypatch, targets, behaviours)

    events = _run(_collect(llm.chat_with_ai_stream([{"role": "user", "content": "hi"}])))

    assert [event.type for event in events] == ["delta"]
    assert events[0].content == "你好"
    assert called == ["main", "backup"]


def test_stream_does_not_switch_after_first_token(monkeypatch):
    targets = [_target("deepseek", "main"), _target("openai", "backup")]
    called = []
    behaviours = {
        "main": _stream_events(
            called,
            "main",
            [StreamDeltaEvent(content="半")],
            error_after=APIConnectionError(request=_request()),
        ),
        "backup": _stream_events(called, "backup", [StreamDeltaEvent(content="不该出现")]),
    }
    _patch_streaming(monkeypatch, targets, behaviours)

    events = _run(_collect(llm.chat_with_ai_stream([{"role": "user", "content": "hi"}])))

    assert [event.type for event in events] == ["delta", "error"]
    assert events[0].content == "半"
    assert called == ["main"]


def test_stream_does_not_switch_on_auth_error(monkeypatch):
    targets = [_target("deepseek", "main"), _target("openai", "backup")]
    called = []
    behaviours = {
        "main": _stream_raiser(called, "main", _status_error(AuthenticationError, 401)),
        "backup": _stream_events(called, "backup", [StreamDeltaEvent(content="不该出现")]),
    }
    _patch_streaming(monkeypatch, targets, behaviours)

    events = _run(_collect(llm.chat_with_ai_stream([{"role": "user", "content": "hi"}])))

    assert [event.type for event in events] == ["error"]
    assert called == ["main"]


def test_stream_uses_fallback_usage_model(monkeypatch):
    """降级后 usage 里的 model 必须是实际命中的备用模型。"""
    targets = [_target("deepseek", "main"), _target("openai", "backup")]
    called = []
    behaviours = {
        "main": _stream_raiser(called, "main", APIConnectionError(request=_request())),
        "backup": _stream_events(
            called,
            "backup",
            [StreamUsageEvent(usage=TokenUsage(model="backup", total_tokens=10))],
        ),
    }
    _patch_streaming(monkeypatch, targets, behaviours)

    events = _run(_collect(llm.chat_with_ai_stream([{"role": "user", "content": "hi"}])))

    assert events[0].type == "usage"
    assert events[0].usage.model == "backup"


# ---------------------------------------------------------------------------
# 非流式降级
# ---------------------------------------------------------------------------


def test_non_stream_falls_back_to_backup_target(monkeypatch):
    targets = [_target("deepseek", "main"), _target("openai", "backup")]
    called = []

    async def fake_run_tool_loop(client, model, messages, **kwargs):
        called.append(model)
        if model == "main":
            raise APIConnectionError(request=_request())
        return messages, "备用回答"

    monkeypatch.setattr(llm, "resolve_model_targets", lambda api_key, provider: targets)
    monkeypatch.setattr(llm, "run_tool_loop", fake_run_tool_loop)
    monkeypatch.setattr(llm, "get_async_client_for", lambda target: SimpleNamespace(model=target.model))

    async def fake_run_blocking(func, *args, **kwargs):
        return (None, "deepseek")

    monkeypatch.setattr(llm, "run_blocking", fake_run_blocking)

    reply = _run(llm.chat_with_ai([{"role": "user", "content": "hi"}]))

    assert reply == "备用回答"
    assert called == ["main", "backup"]
