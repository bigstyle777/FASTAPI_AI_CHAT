"""并发闸门与限流测试。

覆盖：
    全局信号量打满   直接拒绝（不排队），且不建生成任务、不落库
    每用户并发=1     已有 PENDING/RUNNING 时第二条请求被拒（不落用户消息）
    频率限流         阈值来自 settings.chat_rate_limit_per_hour

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_concurrency_limit.py -v
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
from app.chat.schemas import StreamDeltaEvent, TokenUsage, StreamUsageEvent  # noqa: E402
from app.chat.services import messages as messages_mod  # noqa: E402
from app.chat.services.generation import create_generation  # noqa: E402
from app.chat.services.messages import (  # noqa: E402
    modify_message_service,
    send_message_stream_service,
    stream_ai_reply,
)
from app.chat.services.message_persistence import persist_user_message  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    return [event async for event in agen]


def _event_names(events):
    return [e.split("\n", 1)[0].removeprefix("event: ") for e in events]


def _fake_stream():
    async def gen(*args, **kwargs):
        yield StreamDeltaEvent(content="回答")
        yield StreamUsageEvent(
            usage=TokenUsage(model="m", prompt_tokens=1, completion_tokens=2, total_tokens=3)
        )

    return gen


def _chat_request(session_id):
    return SimpleNamespace(session_id=session_id, message="你好")


@pytest.fixture
def session_id(db, user_id):
    return create_session(db, user_id, "并发测试会话").id


@pytest.fixture(autouse=True)
def _stub_memory_enqueue(monkeypatch):
    monkeypatch.setattr(messages_mod, "enqueue_memory_extraction", lambda *a, **k: True)


def test_global_slots_exhausted_rejects_without_creating_generation(
    db, session_id, user_id, monkeypatch
):
    async def scenario():
        slots = asyncio.Semaphore(1)
        await slots.acquire()  # 占满闸门，模拟所有槽位都在跑
        monkeypatch.setattr(messages_mod, "_generation_slots", slots)
        return await _collect(
            stream_ai_reply(db, user_id, session_id, [{"role": "user", "content": "hi"}])
        )

    events = _run(scenario())

    assert _event_names(events) == ["error"]
    assert "请求过多" in events[0]
    # 被拒的请求不建任务、不落库
    assert db.query(ChatGeneration).filter_by(session_id=session_id).count() == 0
    assert [m for m in get_messages_by_session(db, session_id) if m.role == "assistant"] == []


def test_second_submission_rejected_while_user_has_running_generation(
    db, session_id, user_id, monkeypatch
):
    create_generation(db, session_id=session_id, user_id=user_id)
    monkeypatch.setattr(messages_mod, "chat_with_ai_stream", _fake_stream())

    events = _run(_collect(send_message_stream_service(
        db, {"user_id": user_id}, _chat_request(session_id)
    )))

    assert _event_names(events) == ["error"]
    assert "已有回答正在生成" in events[0]
    # 拒绝发生在落用户消息之前，不留半条对话
    assert [m for m in get_messages_by_session(db, session_id) if m.role == "user"] == []


def test_modify_message_rejected_while_user_has_running_generation(
    db, session_id, user_id, monkeypatch
):
    user_message = persist_user_message(db, user_id, session_id, "原始问题")
    create_generation(db, session_id=session_id, user_id=user_id)
    monkeypatch.setattr(messages_mod, "chat_with_ai_stream", _fake_stream())

    events = _run(_collect(modify_message_service(
        db, {"user_id": user_id}, user_message.id, "改过的问题"
    )))

    assert _event_names(events) == ["error"]
    assert "已有回答正在生成" in events[0]
    # 内容没有被提前改写
    assert [m.content for m in get_messages_by_session(db, session_id) if m.role == "user"] == [
        "原始问题"
    ]


def test_rate_limit_threshold_comes_from_settings(db, session_id, user_id, monkeypatch):
    monkeypatch.setattr(messages_mod.settings, "chat_rate_limit_per_hour", 2)
    monkeypatch.setattr(messages_mod, "chat_with_ai_stream", _fake_stream())

    def send():
        return _run(_collect(send_message_stream_service(
            db, {"user_id": user_id}, _chat_request(session_id)
        )))

    assert _event_names(send())[-1] == "done"
    assert _event_names(send())[-1] == "done"
    # 第 3 次超过阈值 2
    rejected = send()
    assert _event_names(rejected) == ["error"]
    assert "请求太频繁" in rejected[0]
