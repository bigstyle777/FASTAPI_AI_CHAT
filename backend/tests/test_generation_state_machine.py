"""生成任务状态机测试（chat/services/generation.py + chat_generations 表）。

覆盖：建任务、幂等键唯一约束、合法/非法流转、崩溃清扫、终态删除重试。

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_generation_state_machine.py -v
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.chat.models import ChatGeneration  # noqa: E402
from app.chat.repository import create_message, create_session  # noqa: E402
from app.chat.services.generation import (  # noqa: E402
    COMPLETED,
    FAILED,
    PENDING,
    RUNNING,
    create_generation,
    delete_generation,
    find_by_idempotency_key,
    get_running_generation,
    sweep_stale_generations,
    transition,
)


@pytest.fixture
def session_id(db, user_id):
    return create_session(db, user_id, "状态机测试会话").id


def test_create_generation_starts_pending(db, session_id, user_id):
    gen = create_generation(db, session_id=session_id, user_id=user_id)

    assert gen.status == PENDING
    assert gen.finished_at is None


def test_duplicate_idempotency_key_returns_none(db, session_id, user_id):
    first = create_generation(
        db, session_id=session_id, user_id=user_id, idempotency_key="k-1"
    )
    second = create_generation(
        db, session_id=session_id, user_id=user_id, idempotency_key="k-1"
    )

    assert first is not None
    assert second is None
    assert db.query(ChatGeneration).filter_by(idempotency_key="k-1").count() == 1


def test_find_by_idempotency_key(db, session_id, user_id):
    gen = create_generation(
        db, session_id=session_id, user_id=user_id, idempotency_key="k-2"
    )

    assert find_by_idempotency_key(db, "k-2").id == gen.id
    assert find_by_idempotency_key(db, "k-not-exists") is None


def test_legal_transition_flow_with_fields(db, session_id, user_id):
    gen = create_generation(db, session_id=session_id, user_id=user_id)

    assert transition(db, gen.id, from_status=(PENDING,), to_status=RUNNING)
    assert transition(
        db, gen.id,
        from_status=(RUNNING,), to_status=COMPLETED,
        model="test-model", prompt_tokens=3, completion_tokens=5, total_tokens=8,
    )

    db.refresh(gen)
    assert gen.status == COMPLETED
    assert gen.model == "test-model"
    assert gen.total_tokens == 8
    assert gen.finished_at is not None


def test_transition_rejects_terminal_to_running(db, session_id, user_id):
    gen = create_generation(db, session_id=session_id, user_id=user_id)
    transition(db, gen.id, from_status=(PENDING,), to_status=RUNNING)
    transition(db, gen.id, from_status=(RUNNING,), to_status=COMPLETED)

    # 终态不可再流转
    assert not transition(db, gen.id, from_status=(PENDING,), to_status=RUNNING)
    db.refresh(gen)
    assert gen.status == COMPLETED


def test_transition_rejects_wrong_source_state(db, session_id, user_id):
    gen = create_generation(db, session_id=session_id, user_id=user_id)

    # 任务还是 PENDING，试图从 RUNNING 落终态 → 拒绝
    assert not transition(db, gen.id, from_status=(RUNNING,), to_status=COMPLETED)
    db.refresh(gen)
    assert gen.status == PENDING
    assert gen.finished_at is None


def test_get_running_generation_ignores_terminal(db, session_id, user_id):
    done_gen = create_generation(db, session_id=session_id, user_id=user_id)
    transition(db, done_gen.id, from_status=(PENDING,), to_status=RUNNING)
    transition(db, done_gen.id, from_status=(RUNNING,), to_status=COMPLETED)
    assert get_running_generation(db, user_id) is None

    running_gen = create_generation(db, session_id=session_id, user_id=user_id)
    transition(db, running_gen.id, from_status=(PENDING,), to_status=RUNNING)
    assert get_running_generation(db, user_id).id == running_gen.id


def test_sweep_marks_only_stale_running_failed(db, session_id, user_id):
    stale = create_generation(db, session_id=session_id, user_id=user_id)
    transition(db, stale.id, from_status=(PENDING,), to_status=RUNNING)
    # 把 updated_at 拨回 20 分钟前，模拟进程崩溃遗留的假 RUNNING
    db.execute(
        update(ChatGeneration)
        .where(ChatGeneration.id == stale.id)
        .values(updated_at=datetime.utcnow() - timedelta(minutes=20))
    )
    db.commit()

    fresh = create_generation(db, session_id=session_id, user_id=user_id)
    transition(db, fresh.id, from_status=(PENDING,), to_status=RUNNING)
    done = create_generation(db, session_id=session_id, user_id=user_id)

    assert sweep_stale_generations(db) == 1

    db.refresh(stale)
    db.refresh(fresh)
    db.refresh(done)
    assert stale.status == FAILED
    assert stale.error == "process_restarted"
    assert fresh.status == RUNNING
    assert done.status == PENDING


def test_delete_terminal_generation_allows_same_key_retry(db, session_id, user_id):
    gen = create_generation(
        db, session_id=session_id, user_id=user_id, idempotency_key="k-3"
    )
    transition(db, gen.id, from_status=(PENDING,), to_status=RUNNING)
    transition(db, gen.id, from_status=(RUNNING,), to_status=FAILED, error="boom")

    # 终态记录删除后，同一幂等键可以作为"重试"重新建任务
    delete_generation(db, gen.id)
    retry = create_generation(
        db, session_id=session_id, user_id=user_id, idempotency_key="k-3"
    )
    assert retry is not None


def test_generation_links_user_message(db, session_id, user_id):
    message = create_message(db, session_id, "user", "你好")
    gen = create_generation(
        db, session_id=session_id, user_id=user_id, user_message_id=message.id
    )

    db.refresh(gen)
    assert gen.user_message_id == message.id
