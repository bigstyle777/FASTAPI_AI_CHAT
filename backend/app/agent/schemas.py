"""Agent 查询接口的响应模型（trace 查看/调试用）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentTracePointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sequence: int
    stage: str
    name: str
    status: str
    step_index: int | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    input_data: dict[str, Any] | None = None
    output_data: dict[str, Any] | None = None
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: int = Field(alias="id")
    session_id: int
    user_id: int
    status: str
    user_input: str
    plan: list[dict[str, Any]] | None = None
    final_answer: str | None = None
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    trace_count: int = 0
    traces: list[AgentTracePointResponse] = Field(default_factory=list)


class AgentRunListResponse(BaseModel):
    success: bool = True
    runs: list[AgentRunResponse] = Field(default_factory=list)
