"""执行器：负责执行计划中的一步。

两种路径：
1. 步骤声明了具体工具 -> 直接调用 execute_tool_call（快路径，不经过 LLM）；
2. 步骤没有工具 -> 让 LLM 带着工具循环去完成该步（run_tool_loop），
   如果工具轮数耗尽则回退到一次不带工具的普通请求。
"""

import json
from typing import Any

from ..services.tool_calling import execute_tool_call, run_tool_loop
from ..tools import TOOL_REGISTRY
from .events import AgentToolEvent
from .prompts import STEP_EXECUTOR_SYSTEM_PROMPT
from .state import PlanStep, StepResult
from .trace import NullTracer


def _tool_callbacks(tracer, step_index: int):
    """把工具调用过程同时落到 trace 表 + 待推送事件里。"""

    def on_tool_call(
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ):
        tracer.point(
            "tool_call",
            tool_name,
            status="started",
            step_index=step_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            input_data={"arguments": arguments},
        )
        tracer.emit(
            AgentToolEvent(
                run_id=tracer.run_id,
                step_index=step_index,
                tool_call_id=tool_call_id,
                tool=tool_name,
                arguments=arguments,
                status="started",
            )
        )

    def on_tool_result(
        tool_call_id: str,
        tool_name: str,
        result: Any,
        *,
        error: str | None = None,
        error_type: str | None = None,
        duration_ms: int | None = None,
    ):
        output = {"result": result}
        if error:
            output = {"error": error, "error_type": error_type}
        tracer.point(
            "tool_call",
            tool_name,
            status="failed" if error else "completed",
            step_index=step_index,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            output_data=output,
            error_message=error,
            duration_ms=duration_ms,
        )
        tracer.emit(
            AgentToolEvent(
                run_id=tracer.run_id,
                step_index=step_index,
                tool_call_id=tool_call_id,
                tool=tool_name,
                status="failed" if error else "completed",
                result=None if error else result,
                error=error,
                duration_ms=duration_ms,
            )
        )

    return on_tool_call, on_tool_result


def _is_tool_error(content: str) -> bool:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and bool(data.get("error"))


def _execute_direct_tool(
    step: PlanStep,
    index: int,
    context: dict | None,
    tracer,
) -> StepResult:
    on_tool_call, on_tool_result = _tool_callbacks(tracer, index)
    tool_call = {
        "id": f"step_{index}_tool",
        "type": "function",
        "function": {
            "name": step.tool,
            "arguments": json.dumps(step.args, ensure_ascii=False),
        },
    }
    try:
        content = execute_tool_call(
            tool_call,
            context,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
        )
        if _is_tool_error(content):
            return StepResult(
                index=index,
                step=step,
                status="failed",
                error=content,
            )
        return StepResult(
            index=index,
            step=step,
            status="completed",
            output=content,
        )
    except Exception as error:  # noqa: BLE001
        return StepResult(
            index=index,
            step=step,
            status="failed",
            error=str(error),
        )


def _build_step_prompt(step: PlanStep, execution_log: list[str]) -> str:
    lines = [f"当前步骤：{step.description}"]
    if step.expected_output:
        lines.append(f"预期产出：{step.expected_output}")
    if execution_log:
        lines.append("之前步骤的结果：")
        lines.extend(f"- {line}" for line in execution_log)
    return "\n".join(lines)


def _execute_with_llm(
    client,
    model: str,
    step: PlanStep,
    index: int,
    context: dict | None,
    tracer,
    execution_log: list[str],
    max_tool_turns: int,
) -> StepResult:
    on_tool_call, on_tool_result = _tool_callbacks(tracer, index)
    system_message = {"role": "system", "content": STEP_EXECUTOR_SYSTEM_PROMPT}
    user_message = {"role": "user", "content": _build_step_prompt(step, execution_log)}

    with tracer.span(
        "llm",
        f"step_{index}_llm",
        step_index=index,
        input_data={"messages": [system_message, user_message]},
    ) as record:
        try:
            history, content = run_tool_loop(
                client,
                model,
                [system_message, user_message],
                context=context,
                max_turns=max_tool_turns,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
            )
            if content is None:
                # 工具轮数耗尽等场景，回退到一次不带工具的普通请求
                fallback = client.chat.completions.create(
                    model=model,
                    messages=history,
                )
                content = fallback.choices[0].message.content or ""
            record.set({"output": content, "history_length": len(history)})
            return StepResult(
                index=index,
                step=step,
                status="completed",
                output=content,
            )
        except Exception as error:  # noqa: BLE001
            record.set({"error": str(error)})
            return StepResult(
                index=index,
                step=step,
                status="failed",
                error=str(error),
            )


def execute_step(
    client,
    model: str,
    step: PlanStep,
    *,
    index: int,
    context: dict | None = None,
    tracer=None,
    execution_log: list[str] | None = None,
    max_tool_turns: int = 5,
) -> StepResult:
    if tracer is None:
        tracer = NullTracer()
    if step.tool and step.tool in TOOL_REGISTRY:
        return _execute_direct_tool(step, index, context, tracer)
    return _execute_with_llm(
        client,
        model,
        step,
        index,
        context,
        tracer,
        execution_log or [],
        max_tool_turns,
    )
