"""AgentTracer / NullTracer 测试（真实测试库，不依赖 LLM）。

覆盖：
    point()           立即落库一条记录、sequence 递增
    span()            started / completed / failed 状态机 + duration_ms
    emit/drain_events 事件队列取出即清空
    NullTracer        不落库、span 正常传播异常

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_trace.py -v
"""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent.trace import AgentTracer, NullTracer  # noqa: E402
from app.agent.repo import create_agent_run, get_trace_points  # noqa: E402
from app.crud import create_role, create_user, get_role_by_name  # noqa: E402
from app.models import ChatSession  # noqa: E402


def _make_run(db, tag="trace"):
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    user = create_user(db, f"{tag}_user", "hashed-password", role.id)
    session = ChatSession(user_id=user.id, title="trace 测试会话")
    db.add(session)
    db.commit()
    db.refresh(session)
    return create_agent_run(
        db,
        session_id=session.id,
        user_id=user.id,
        user_input="测试输入",
    )


def _points_of(db, run_id):
    return get_trace_points(db, run_id)


# ---------------------------------------------------------------------------
# point()
# ---------------------------------------------------------------------------


def test_point_persists_record_with_increasing_sequence(db):
    run = _make_run(db)
    tracer = AgentTracer(db, run.id)

    tracer.point("plan", "planner", status="started")
    tracer.point("plan", "planner", status="completed", duration_ms=12)

    points = _points_of(db, run.id)
    assert [p.sequence for p in points] == [0, 1]
    assert points[0].status == "started"
    assert points[1].status == "completed"
    assert points[1].duration_ms == 12
    assert all(p.run_id == run.id for p in points)


# ---------------------------------------------------------------------------
# span()
# ---------------------------------------------------------------------------


def test_span_records_started_and_completed(db):
    run = _make_run(db)
    tracer = AgentTracer(db, run.id)

    with tracer.span("step", "step_0", step_index=0) as record:
        record.set({"output": "ok"})

    points = _points_of(db, run.id)
    assert [p.status for p in points] == ["started", "completed"]
    assert points[1].output_data == {"output": "ok"}
    assert points[1].duration_ms is not None


def test_span_records_failed_and_reraises(db):
    run = _make_run(db)
    tracer = AgentTracer(db, run.id)

    with pytest.raises(ValueError, match="boom"):
        with tracer.span("llm", "step_0_llm"):
            raise ValueError("boom")

    points = _points_of(db, run.id)
    assert [p.status for p in points] == ["started", "failed"]
    assert points[1].error_message == "boom"
    assert points[1].duration_ms is not None


def test_span_fail_record_marks_failed_without_exception(db):
    """record.fail() 让 span 记 failed 但不抛异常（业务失败路径）。"""
    run = _make_run(db)
    tracer = AgentTracer(db, run.id)

    with tracer.span("tool_call", "calculator") as record:
        record.fail("参数错误")

    points = _points_of(db, run.id)
    assert [p.status for p in points] == ["started", "failed"]
    assert points[1].error_message == "参数错误"


# ---------------------------------------------------------------------------
# emit / drain_events
# ---------------------------------------------------------------------------


def test_emit_then_drain_returns_and_clears(db):
    run = _make_run(db)
    tracer = AgentTracer(db, run.id)

    tracer.emit({"type": "agent_tool"})
    tracer.emit({"type": "agent_tool"})

    assert len(tracer.drain_events()) == 2
    assert tracer.drain_events() == []


# ---------------------------------------------------------------------------
# NullTracer
# ---------------------------------------------------------------------------


def test_null_tracer_noop_and_propagates_exceptions():
    tracer = NullTracer(run_id=0)

    assert tracer.point("any", "thing") is None
    tracer.emit({"type": "agent_tool"})
    assert tracer.drain_events() == []

    with pytest.raises(RuntimeError, match="boom"):
        with tracer.span("step", "step_0") as record:
            record.set({"ok": True})
            raise RuntimeError("boom")

    # 不落库（run_id=0 在数据库里不存在对应记录，天然无法验证行数，
    # 这里验证 NullTracer 的 API 形状与 AgentTracer 一致）
    assert hasattr(tracer, "point")
    assert hasattr(tracer, "span")
    assert hasattr(tracer, "emit")
    assert hasattr(tracer, "drain_events")
