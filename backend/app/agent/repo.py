"""agent_runs / agent_trace_points 的数据库访问。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AgentRun, AgentTracePoint


def create_agent_run(
    db: Session,
    *,
    session_id: int,
    user_id: int,
    user_input: str,
) -> AgentRun:
    run = AgentRun(
        session_id=session_id,
        user_id=user_id,
        user_input=user_input,
        status="running",
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def update_agent_run(
    db: Session,
    run_id: int,
    *,
    status: str | None = None,
    plan: list[dict] | None = None,
    final_answer: str | None = None,
    model: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    error_message: str | None = None,
) -> AgentRun | None:
    run = db.get(AgentRun, run_id)
    if run is None:
        return None
    if status is not None:
        run.status = status
    if plan is not None:
        run.plan = plan
    if final_answer is not None:
        run.final_answer = final_answer
    if model is not None:
        run.model = model
    if prompt_tokens is not None:
        run.prompt_tokens = prompt_tokens
    if completion_tokens is not None:
        run.completion_tokens = completion_tokens
    if total_tokens is not None:
        run.total_tokens = total_tokens
    if error_message is not None:
        run.error_message = error_message
    db.commit()
    db.refresh(run)
    return run


def get_agent_run(db: Session, run_id: int, user_id: int | None = None) -> AgentRun | None:
    stmt = select(AgentRun).where(AgentRun.id == run_id)
    if user_id is not None:
        stmt = stmt.where(AgentRun.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def list_agent_runs(
    db: Session,
    user_id: int,
    *,
    session_id: int | None = None,
    limit: int = 20,
) -> list[AgentRun]:
    stmt = select(AgentRun).where(AgentRun.user_id == user_id)
    if session_id is not None:
        stmt = stmt.where(AgentRun.session_id == session_id)
    stmt = stmt.order_by(AgentRun.id.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_trace_points(db: Session, run_id: int) -> list[AgentTracePoint]:
    stmt = (
        select(AgentTracePoint)
        .where(AgentTracePoint.run_id == run_id)
        .order_by(AgentTracePoint.sequence.asc())
    )
    return list(db.execute(stmt).scalars().all())


def count_trace_points(db: Session, run_id: int) -> int:
    stmt = select(func.count(AgentTracePoint.id)).where(
        AgentTracePoint.run_id == run_id
    )
    return int(db.execute(stmt).scalar_one())
