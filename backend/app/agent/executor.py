"""执行器：负责执行计划中的一步。

两种路径：
1. 步骤声明了具体工具 -> 直接调用 execute_tool_call（快路径，不经过 LLM）；
2. 步骤没有工具 -> 让 LLM 带着工具循环去完成该步（run_tool_loop），
   如果工具轮数耗尽则回退到一次不带工具的普通请求。
"""

import json
from typing import Any

from ..chat.schemas import TokenUsage
from ..core.config import settings
from ..core.llm_gateway import complete
from ..tools import TOOL_REGISTRY
from .events import AgentToolEvent
from .prompts import STEP_EXECUTOR_SYSTEM_PROMPT
from .state import PlanStep, StepResult
from .tool_calling import execute_tool_call, run_tool_loop
from .trace import NullTracer
from .usage import accumulate_usage, snapshot_usage


def _tool_callbacks(tracer, step_index: int):
    """把工具调用过程同时落到 trace 表 + 待推送事件里。

    两个回调都是异步的：落库是同步 DB 操作，要走线程池不阻塞事件循环。
    """

    async def on_tool_call(
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ):
        await tracer.point(
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

    async def on_tool_result(
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
        await tracer.point(
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


async def _execute_direct_tool(
    step: PlanStep,
    index: int,
    context: dict | None,
    tracer,
    *,
    timeout_seconds: float | None = None,
    max_output_chars: int | None = None,
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
        content = await execute_tool_call(
            tool_call,
            context,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
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


def _usage_delta(before, after, provider: str | None) -> dict[str, Any]:
    """这一步 LLM 调用消耗的 token（前后累计差值），写进 trace。"""
    if before is None or after is None:
        return {}
    return {
        "prompt_tokens": after.prompt_tokens - before.prompt_tokens,
        "completion_tokens": after.completion_tokens - before.completion_tokens,
        "total_tokens": after.total_tokens - before.total_tokens,
        "model": after.model,
        "provider": after.provider or provider,
    }


def _build_step_prompt(step: PlanStep, execution_log: list[str]) -> str:
    lines = [f"当前步骤：{step.description}"]
    if step.expected_output:
        lines.append(f"预期产出：{step.expected_output}")
    if execution_log:
        lines.append("之前步骤的结果：")
        lines.extend(f"- {line}" for line in execution_log)
    return "\n".join(lines)


async def _execute_with_llm(
    client,
    model: str,
    step: PlanStep,
    index: int,
    context: dict | None,
    tracer,
    execution_log: list[str],
    max_tool_turns: int,
    tool_error: str | None = None,
    targets=None,
    usage_sink: TokenUsage | None = None,
    provider: str | None = None,
    timeout_seconds: float | None = None,
    max_output_chars: int | None = None,
) -> StepResult:
    on_tool_call, on_tool_result = _tool_callbacks(tracer, index)
    system_message = {"role": "system", "content": STEP_EXECUTOR_SYSTEM_PROMPT}
    prompt = _build_step_prompt(step, execution_log)
    if tool_error:
        prompt += (
            "\n\n注意：直接调用工具失败，错误信息如下。"
            "请对照工具 schema 修正参数后重新调用；"
            "如果确实无法完成，基于已有信息给出当前结论。\n"
            f"{tool_error}"
        )
    user_message = {"role": "user", "content": prompt}

    async with tracer.span(
        "llm",
        f"step_{index}_llm",
        step_index=index,
        input_data={"messages": [system_message, user_message]},
    ) as record:
        before = snapshot_usage(usage_sink) if usage_sink is not None else None
        try:
            circuit_target = (
                (targets[0].provider, targets[0].base_url) if targets else None
            )
            history, content = await run_tool_loop(
                client,
                model,
                [system_message, user_message],
                context=context,
                max_turns=max_tool_turns,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                circuit_target=circuit_target,
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output_chars,
                usage_sink=usage_sink,
                provider=provider,
            )
            if content is None:
                # 工具轮数耗尽等场景，回退到一次不带工具的普通请求
                if targets:
                    result = await complete(targets, messages=history)
                    fallback, used_target = result.response, result.target
                    accumulate_usage(
                        usage_sink,
                        getattr(fallback, "usage", None),
                        model=used_target.model,
                        provider=used_target.provider,
                    )
                else:
                    fallback = await client.chat.completions.create(
                        model=model,
                        messages=history,
                    )
                    accumulate_usage(
                        usage_sink,
                        getattr(fallback, "usage", None),
                        model=model,
                        provider=provider,
                    )
                content = fallback.choices[0].message.content or ""
            record.set(
                {
                    "output": content,
                    "history_length": len(history),
                    **_usage_delta(before, usage_sink, provider),
                }
            )
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


async def execute_step(
    client,
    model: str,
    step: PlanStep,
    *,
    index: int,
    context: dict | None = None,
    tracer=None,
    execution_log: list[str] | None = None,
    max_tool_turns: int = 5,
    targets=None,
    usage_sink: TokenUsage | None = None,
    provider: str | None = None,
    timeout_seconds: float | None = None,
    max_output_chars: int | None = None,
) -> StepResult:
    if tracer is None:
        tracer = NullTracer()
    if timeout_seconds is None:
        timeout_seconds = settings.agent_tool_timeout_seconds
    if max_output_chars is None:
        max_output_chars = settings.agent_tool_output_max_chars

    if step.tool and step.tool in TOOL_REGISTRY:
        result = await _execute_direct_tool(
            step,
            index,
            context,
            tracer,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )
        if result.status == "failed" and result.error:
            # 自愈：工具调用失败（通常是参数错误），带着错误信息让 LLM 重试一次
            return await _execute_with_llm(
                client,
                model,
                step,
                index,
                context,
                tracer,
                execution_log or [],
                max_tool_turns,
                tool_error=result.error,
                targets=targets,
                usage_sink=usage_sink,
                provider=provider,
                timeout_seconds=timeout_seconds,
                max_output_chars=max_output_chars,
            )
        return result
    return await _execute_with_llm(
        client,
        model,
        step,
        index,
        context,
        tracer,
        execution_log or [],
        max_tool_turns,
        targets=targets,
        usage_sink=usage_sink,
        provider=provider,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )
