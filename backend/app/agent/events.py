"""Agent 专用 SSE 事件载荷。

事件命名约定与聊天流保持一致：SSE event 名 = data 里的 type 字段。
`delta` / `usage` / `error` 直接复用 app.schemas 里已有的聊天事件类型，
前端 `consumeStream` 无需改动即可拿到最终答案。
"""

from typing import Any, Literal

from pydantic import BaseModel


class AgentPlanEvent(BaseModel):
    type: Literal["agent_plan"] = "agent_plan"
    run_id: int
    steps: list[dict[str, Any]]


class AgentStepEvent(BaseModel):
    type: Literal["agent_step"] = "agent_step"
    run_id: int
    index: int
    step: dict[str, Any]
    status: Literal["started", "completed", "failed", "skipped"]
    output: str | None = None
    error: str | None = None


class AgentToolEvent(BaseModel):
    type: Literal["agent_tool"] = "agent_tool"
    run_id: int
    step_index: int
    tool_call_id: str | None = None
    tool: str
    arguments: dict[str, Any] | None = None
    status: Literal["started", "completed", "failed"]
    result: Any | None = None
    error: str | None = None
    duration_ms: int | None = None


class AgentDoneEvent(BaseModel):
    type: Literal["done"] = "done"
    run_id: int
    status: Literal["completed", "stopped", "failed"]
