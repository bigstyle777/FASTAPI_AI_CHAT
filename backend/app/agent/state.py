"""Agent 运行时的状态模型。

`PlanStep` / `StepResult` 是执行过程中的核心数据对象；
`AgentState` 是整轮运行的可序列化快照（供非流式调用与测试使用）。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    """计划中的一个步骤。tool 为空时表示该步直接让 LLM 处理。"""

    description: str
    tool: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    expected_output: str | None = None


class StepResult(BaseModel):
    index: int
    step: PlanStep
    status: Literal["completed", "failed", "skipped"]
    output: str | None = None
    error: str | None = None


# 测试用
@dataclass
class AgentState:
    run_id: int
    user_input: str
    plan: list[PlanStep] = field(default_factory=list)
    results: list[StepResult] = field(default_factory=list)
    status: str = "running"
    final_answer: str | None = None
    error: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
