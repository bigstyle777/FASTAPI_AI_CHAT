"""流式生成可靠性测试：状态机 + 幂等在真实 DB 上的端到端行为。

monkeypatch 掉 chat_with_ai_stream 造可控假流，验证 stream_ai_reply /
send_message_stream_service 在各退出路径上的状态流转与资源清理。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_stream_reliability.py -v
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.chat.models import ChatGeneration  # noqa: E402
from app.chat.repository import create_session, get_messages_by_session  # noqa: E402
from app.chat.schemas import (  # noqa: E402
    StreamDeltaEvent,
    StreamErrorEvent,
    StreamUsageEvent,
    TokenUsage,
)
from app.chat.services import messages as messages_mod  # noqa: E402
from app.chat.services.generation import (  # noqa: E402
    CANCELLED,
    COMPLETED,
    FAILED,
    TIMED_OUT,
)
from app.chat.services.messages import (  # noqa: E402
    send_message_stream_service,
    stream_ai_reply,
)
from app.core.stop_signal import mark_stopped  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    return [event async for event in agen]


def _event_names(events):
    """sse_event 产出的是 "event: xxx\ndata: ..." 字符串，取事件名列表。"""
    return [e.split("\n", 1)[0].removeprefix("event: ") for e in events]


def _usage():
    return StreamUsageEvent(
        usage=TokenUsage(model="m", prompt_tokens=3, completion_tokens=5, total_tokens=8)
    )


def _fake_stream(events):
    async def gen(*args, **kwargs):
        for event in events:
            yield event

    return gen


@pytest.fixture
def session_id(db, user_id):
    return create_session(db, user_id, "可靠性测试会话").id


@pytest.fixture(autouse=True)
def _stub_memory_enqueue(monkeypatch):
    """记忆入队走 Celery，本测试范围外，换成空操作。"""
    monkeypatch.setattr(messages_mod, "enqueue_memory_extraction", lambda *a, **k: True)


def _only_generation(db, session_id):
    return (
        db.query(ChatGeneration)
        .filter_by(session_id=session_id)
        .order_by(ChatGeneration.id.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# stream_ai_reply：各退出路径的状态机
# ---------------------------------------------------------------------------


def test_completed_generation_records_tokens_and_reply(db, session_id, user_id, monkeypatch):
    monkeypatch.setattr(
        messages_mod,
        "chat_with_ai_stream",
        _fake_stream([
            StreamDeltaEvent(content="你好"),
            StreamDeltaEvent(content="世界"),
            _usage(),
        ]),
    )

    events = _run(_collect(stream_ai_reply(db, user_id, session_id, [{"role": "user", "content": "hi"}])))

    assert _event_names(events) == ["delta", "delta", "usage", "done"]

    generation = _only_generation(db, session_id)
    assert generation.status == COMPLETED
    assert generation.total_tokens == 8

    replies = [m for m in get_messages_by_session(db, session_id) if m.role == "assistant"]
    assert replies[0].content == "你好世界"


def test_stream_error_marks_failed_without_persisting(db, session_id, user_id, monkeypatch):
    monkeypatch.setattr(
        messages_mod,
        "chat_with_ai_stream",
        _fake_stream([
            StreamDeltaEvent(content="半截"),
            StreamErrorEvent(message="上游炸了"),
        ]),
    )

    events = _run(_collect(stream_ai_reply(db, user_id, session_id, [{"role": "user", "content": "hi"}])))

    assert _event_names(events) == ["delta", "error"]

    generation = _only_generation(db, session_id)
    assert generation.status == FAILED
    assert generation.error == "上游炸了"
    # 错误路径不落库 assistant 消息
    assert [m for m in get_messages_by_session(db, session_id) if m.role == "assistant"] == []


def test_user_stop_marks_cancelled_with_partial_reply(db, session_id, user_id, monkeypatch):
    # 用户点停止发生在流中途：第一段内容之后再置停止标记，
    # 流式循环在下一个事件上就会发现
    async def stopping_stream(*args, **kwargs):
        yield StreamDeltaEvent(content="字0")
        mark_stopped(session_id)
        for i in range(1, 10):
            yield StreamDeltaEvent(content=f"字{i}")

    monkeypatch.setattr(messages_mod, "chat_with_ai_stream", stopping_stream)

    events = _run(_collect(stream_ai_reply(db, user_id, session_id, [{"role": "user", "content": "hi"}])))

    generation = _only_generation(db, session_id)
    assert generation.status == CANCELLED
    # 用户停止：客户端还在，usage/done 照常发
    assert _event_names(events)[-2:] == ["usage", "done"]


def test_stream_timeout_marks_timed_out(db, session_id, user_id, monkeypatch):
    # 总预算设为 -1：第一个事件就超时
    monkeypatch.setattr(messages_mod.settings, "llm_stream_max_seconds", -1)
    monkeypatch.setattr(
        messages_mod,
        "chat_with_ai_stream",
        _fake_stream([StreamDeltaEvent(content="永远写不完")]),
    )

    _run(_collect(stream_ai_reply(db, user_id, session_id, [{"role": "user", "content": "hi"}])))

    generation = _only_generation(db, session_id)
    assert generation.status == TIMED_OUT


def test_disconnect_marks_cancelled_and_skips_async_tail(db, session_id, user_id, monkeypatch):
    async def disconnected():
        return True

    monkeypatch.setattr(
        messages_mod,
        "chat_with_ai_stream",
        _fake_stream([StreamDeltaEvent(content=f"字{i}") for i in range(10)]),
    )

    events = _run(_collect(stream_ai_reply(
        db, user_id, session_id, [{"role": "user", "content": "hi"}],
        is_disconnected=disconnected,
    )))

    generation = _only_generation(db, session_id)
    assert generation.status == CANCELLED
    # 断连后不再发任何事件、不做记忆提取等异步收尾
    assert events == []


def test_unexpected_exception_marks_failed_and_reraises(db, session_id, user_id, monkeypatch):
    async def boom_stream(*args, **kwargs):
        raise RuntimeError("意想不到")
        yield  # pragma: no cover - 让它成为异步生成器

    monkeypatch.setattr(messages_mod, "chat_with_ai_stream", boom_stream)

    with pytest.raises(RuntimeError):
        _run(_collect(stream_ai_reply(db, user_id, session_id, [{"role": "user", "content": "hi"}])))

    generation = _only_generation(db, session_id)
    assert generation.status == FAILED
    assert "RuntimeError" in generation.error


# ---------------------------------------------------------------------------
# send_message_stream_service：幂等
# ---------------------------------------------------------------------------


def _chat_request(session_id):
    return SimpleNamespace(session_id=session_id, message="你好")


def test_duplicate_idempotency_key_replays_without_regenerating(db, session_id, user_id, monkeypatch):
    calls = {"n": 0}

    async def counting_stream(*args, **kwargs):
        calls["n"] += 1
        yield StreamDeltaEvent(content="回答内容")
        yield _usage()

    monkeypatch.setattr(messages_mod, "chat_with_ai_stream", counting_stream)
    user = {"user_id": user_id}

    first = _run(_collect(send_message_stream_service(
        db, user, _chat_request(session_id), idempotency_key="key-dup"
    )))
    assert _event_names(first) == ["delta", "usage", "done"]
    assert calls["n"] == 1

    second = _run(_collect(send_message_stream_service(
        db, user, _chat_request(session_id), idempotency_key="key-dup"
    )))
    # 没有再次调用模型，直接重放已落库的回答
    assert calls["n"] == 1
    assert "回答内容" in second[0]
    assert _event_names(second) == ["delta", "usage", "done"]
    # 用户消息也只落了一条
    user_messages = [m for m in get_messages_by_session(db, session_id) if m.role == "user"]
    assert len(user_messages) == 1


def test_running_generation_rejects_duplicate_submission(db, session_id, user_id, monkeypatch):
    from app.chat.services.generation import create_generation

    create_generation(
        db, session_id=session_id, user_id=user_id, idempotency_key="key-run"
    )  # PENDING 即视为"进行中"

    user = {"user_id": user_id}
    events = _run(_collect(send_message_stream_service(
        db, user, _chat_request(session_id), idempotency_key="key-run"
    )))

    assert _event_names(events) == ["error"]
    assert "请勿重复提交" in events[0]
