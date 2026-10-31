"""生成任务状态机：一次流式生成的全生命周期。

权威数据源是 chat_generations 表（PG）。状态流转全部走条件 UPDATE
（``WHERE status IN (:from_status)``）：受影响行数为 0 说明状态已被并发
改写，记 warning 并以 DB 为准，不强行覆盖——这是崩溃、消息重投场景下
防止重复执行的关键保证。

Redis 里的 stop_requested 只是易失的"停止信号"，不是状态，不在此模块管。
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..models import ChatGeneration

logger = logging.getLogger(__name__)

# 状态枚举。终态不可再流转（流转函数的 from_status 不含终态）。
PENDING = "PENDING"
RUNNING = "RUNNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
TIMED_OUT = "TIMED_OUT"

TERMINAL_STATUSES = (COMPLETED, FAILED, CANCELLED, TIMED_OUT)

# 崩溃清扫的判定阈值：RUNNING/PENDING 超过该时长没有心跳更新，认定进程已死
_SWEEP_STALE_SECONDS = 600


def create_generation(
    db,
    *,
    session_id: int,
    user_id: int,
    user_message_id: int | None = None,
    idempotency_key: str | None = None,
) -> ChatGeneration | None:
    """建 PENDING 任务。幂等键撞唯一索引时返回 None（并发重复提交的兜底）。"""
    generation = ChatGeneration(
        session_id=session_id,
        user_id=user_id,
        user_message_id=user_message_id,
        idempotency_key=idempotency_key,
        status=PENDING,
    )
    db.add(generation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    db.refresh(generation)
    return generation


def find_by_idempotency_key(db, idempotency_key: str) -> ChatGeneration | None:
    stmt = select(ChatGeneration).where(
        ChatGeneration.idempotency_key == idempotency_key
    )
    return db.execute(stmt).scalar_one_or_none()


def get_running_generation(db, user_id: int) -> ChatGeneration | None:
    """查用户当前进行中的生成任务（每用户并发=1 的检查依据）。"""
    stmt = (
        select(ChatGeneration)
        .where(
            ChatGeneration.user_id == user_id,
            ChatGeneration.status.in_([PENDING, RUNNING]),
        )
        .order_by(ChatGeneration.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def delete_generation(db, generation_id: int) -> None:
    """删除终态任务记录：失败/取消后的同键重试需要先清掉旧记录。"""
    generation = db.get(ChatGeneration, generation_id)
    if generation is not None:
        db.delete(generation)
        db.commit()


def transition(
    db,
    generation_id: int,
    *,
    from_status: tuple[str, ...],
    to_status: str,
    **fields,
) -> bool:
    """状态流转唯一入口：条件更新，返回 False = 状态已被并发改写。

    终态流转自动写 finished_at；其余字段（error / model / *_tokens）按需传入。
    """
    values = {"status": to_status, "updated_at": datetime.utcnow()}
    if to_status in TERMINAL_STATUSES:
        values["finished_at"] = datetime.utcnow()
    values.update(fields)
    result = db.execute(
        update(ChatGeneration)
        .where(
            ChatGeneration.id == generation_id,
            ChatGeneration.status.in_(from_status),
        )
        .values(**values)
    )
    db.commit()
    if result.rowcount == 0:
        logger.warning(
            "生成任务 %d 状态流转被拒绝：%s -> %s（状态已被并发改写）",
            generation_id,
            from_status,
            to_status,
        )
        return False
    return True


def sweep_stale_generations(db) -> int:
    """把进程崩溃留下的假 PENDING/RUNNING 落为 FAILED，启动时调用一次。

    条件更新本身是幂等的：多实例同时启动并发执行也安全。
    """
    cutoff = datetime.utcnow() - timedelta(seconds=_SWEEP_STALE_SECONDS)
    now = datetime.utcnow()
    result = db.execute(
        update(ChatGeneration)
        .where(
            ChatGeneration.status.in_([PENDING, RUNNING]),
            ChatGeneration.updated_at < cutoff,
        )
        .values(
            status=FAILED,
            error="process_restarted",
            updated_at=now,
            finished_at=now,
        )
    )
    db.commit()
    if result.rowcount:
        logger.warning(
            "清扫崩溃遗留的生成任务 %d 个（FAILED/process_restarted）", result.rowcount
        )
    return result.rowcount
