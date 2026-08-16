"""Agent trace：把运行过程的关键节点写入数据库，方便事后查错。"""

import time
from contextlib import contextmanager
from typing import Any

from sqlalchemy.orm import Session

from ..models import AgentTracePoint


class AgentTracer:
    """向 agent_runs 关联的 agent_trace_points 表追加 trace 点。

    - `point()` 立即写一条记录；
    - `span()` 自动记录 started / completed / failed 两条记录并附带耗时；
    - `emit()` 收集需要推送给前端的结构化事件（如工具调用），
      由 agent 主循环在合适的时机 drain 并 yield。
    """

    def __init__(self, db: Session, run_id: int):
        self.db = db
        self.run_id = run_id
        self._sequence = 0
        self._pending_events: list[Any] = []

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

    def point(
        self,
        stage: str,
        name: str,
        *,
        status: str = "completed",
        step_index: int | None = None,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        input_data: dict | None = None,
        output_data: dict | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> AgentTracePoint:
        point = AgentTracePoint(
            run_id=self.run_id,
            sequence=self._next_sequence(),
            stage=stage,
            name=name,
            status=status,
            step_index=step_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            input_data=input_data,
            output_data=output_data,
            error_message=error_message,
            duration_ms=duration_ms,
        )
        self.db.add(point)
        self.db.commit()
        return point

    @contextmanager
    def span(
        self,
        stage: str,
        name: str,
        *,
        step_index: int | None = None,
        tool_name: str | None = None,
        input_data: dict | None = None,
    ):
        """记录一段工作：开始一条 started，结束一条 completed / failed。"""
        started_at = time.perf_counter()
        self.point(
            stage,
            name,
            status="started",
            step_index=step_index,
            tool_name=tool_name,
            input_data=input_data,
        )
        record = _SpanRecord()
        try:
            yield record
        except Exception as error:
            duration_ms = _elapsed_ms(started_at)
            self.point(
                stage,
                name,
                status="failed",
                step_index=step_index,
                tool_name=tool_name,
                output_data=record.value,
                error_message=str(error),
                duration_ms=duration_ms,
            )
            raise
        else:
            duration_ms = _elapsed_ms(started_at)
            self.point(
                stage,
                name,
                status="failed" if record.failed else "completed",
                step_index=step_index,
                tool_name=tool_name,
                output_data=record.value,
                error_message=record.error,
                duration_ms=duration_ms,
            )

    def emit(self, event: Any) -> None:
        """收集一个需要推送给前端的结构化事件。"""
        self._pending_events.append(event)

    def drain_events(self) -> list[Any]:
        """取出并清空待推送事件。"""
        events = self._pending_events
        self._pending_events = []
        return events


class _SpanRecord:
    def __init__(self):
        self.value: dict | None = None
        self.failed: bool = False
        self.error: str | None = None

    def set(self, value: dict | None):
        self.value = value

    def fail(self, error: str):
        self.failed = True
        self.error = error


class NullTracer:
    """不落库的兜底 tracer：executor/finalizer 在没有 tracer 时也能跑。"""

    def __init__(self, run_id: int = 0):
        self.run_id = run_id

    def point(self, *args, **kwargs):
        return None

    @contextmanager
    def span(self, *args, **kwargs):
        record = _SpanRecord()
        try:
            yield record
        except Exception:
            raise

    def emit(self, event) -> None:
        pass

    def drain_events(self) -> list:
        return []


def _elapsed_ms(started_at: float) -> int:
    return int((time.perf_counter() - started_at) * 1000)
