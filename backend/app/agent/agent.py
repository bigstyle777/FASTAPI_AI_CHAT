"""Agent 主循环：规划 -> 逐步执行 -> 总结。

`run_agent_stream` 是唯一的数据流入口，逐条 yield pydantic 事件
（AgentPlanEvent / AgentStepEvent / AgentToolEvent / delta / usage），
由 service 层转成 SSE 字符串；trace 由传入的 AgentTracer 负责落库。
"""

from time import perf_counter
from typing import Any, AsyncGenerator

from ..chat.schemas import StreamDeltaEvent, StreamErrorEvent, StreamUsageEvent, TokenUsage
from ..tools import ALL_TOOLS
from .events import AgentPlanEvent, AgentStepEvent
from .executor import execute_step
from .finalizer import stream_final_answer
from .planner import create_plan
from .state import AgentState, PlanStep, StepResult
from .trace import NullTracer


def _build_summary_message(
    user_input: str,
    plan: list[PlanStep],
    results: list[StepResult],
) -> dict[str, str]:
    lines = [
        f"用户问题：{user_input}",
        "计划与执行结果：",
    ]
    for index, step in enumerate(plan):
        result = results[index] if index < len(results) else None
        status = result.status if result else "skipped"
        detail = (result.output or result.error or "（未执行）") if result else "（未执行）"
        lines.append(f"{index + 1}. {step.description} -> {status}: {detail}")
    lines.append("请根据以上内容回答用户问题。")
    return {"role": "user", "content": "\n".join(lines)}


async def run_agent_stream(
    client,
    model: str,
    user_input: str,
    *,
    messages: list[dict],
    context: dict | None = None,
    tracer=None,
    should_stop=None,
    max_steps: int = 6,
    max_tool_turns: int = 5,
    targets=None,
    provider: str | None = None,
    deadline: float | None = None,
    usage: TokenUsage | None = None,
) -> AsyncGenerator[Any, None]:
    """Agent 主循环。yield 的事件由上层转成 SSE。

    targets 传入阶段 2 的模型目标链；deadline 是整轮软超时（绝对
    perf_counter 时间）。两者都不传时退回旧的单目标行为（兼容测试）。
    """
    if tracer is None:
        tracer = NullTracer()
    total_usage = usage if usage is not None else TokenUsage()
    # ---- 1. 规划 ----
    try:
        async with tracer.span(
            "plan",
            "planner",
            input_data={"messages": messages},
        ) as record:
            plan = await create_plan(
                client,
                model,
                messages,
                available_tools=ALL_TOOLS,  # type: ignore
                max_steps=max_steps,
                targets=targets,
                usage_sink=total_usage,
                provider=provider,
            )
            record.set(
                {
                    "steps": [step.model_dump() for step in plan],
                    "prompt_tokens": total_usage.prompt_tokens,
                    "completion_tokens": total_usage.completion_tokens,
                    "total_tokens": total_usage.total_tokens,
                    "model": total_usage.model,
                    "provider": total_usage.provider or provider,
                }
            )
    except Exception as error:  # noqa: BLE001
        yield StreamErrorEvent(message=f"Agent 规划失败：{error}")
        return

    if not plan:
        await tracer.point(
            "plan",
            "planner",
            status="failed",
            error_message="规划器未生成任何步骤",
        )
        yield StreamErrorEvent(
            message="Agent 未能为任务生成计划，请换一种表达方式再试。"
        )
        return

    yield AgentPlanEvent(
        run_id=tracer.run_id,
        steps=[step.model_dump() for step in plan],
    )

    # ---- 2. 逐步执行 ----
    results: list[StepResult] = []
    execution_log: list[str] = []
    stopped = False
    timed_out = False
    step_failed = False
    step_error: str | None = None

    for index, step in enumerate(plan[:max_steps]):
        if deadline is not None and perf_counter() >= deadline:
            timed_out = True
            break
        if should_stop is not None and await should_stop():
            stopped = True
            break

        yield AgentStepEvent(
            run_id=tracer.run_id,
            index=index,
            step=step.model_dump(),
            status="started",
        )

        async with tracer.span(
            "step",
            f"step_{index}",
            step_index=index,
            input_data=step.model_dump(),
        ) as record:
            result = await execute_step(
                client,
                model,
                step,
                index=index,
                context=context,
                tracer=tracer,
                execution_log=execution_log,
                max_tool_turns=max_tool_turns,
                targets=targets,
                usage_sink=total_usage,
                provider=provider,
            )
            record.set(result.model_dump())
            if result.status == "failed" and result.error:
                record.fail(result.error)

        results.append(result)
        if result.status == "completed" and result.output:
            execution_log.append(f"{index + 1}. {step.description}: {result.output}")
        else:
            execution_log.append(
                f"{index + 1}. {step.description}: 失败 - {result.error}"
            )

        # 执行过程中产生的工具事件（agent_tool）在步骤结束后统一推送
        for event in tracer.drain_events():
            yield event

        yield AgentStepEvent(
            run_id=tracer.run_id,
            index=index,
            step=step.model_dump(),
            status=result.status,
            output=result.output,
            error=result.error,
        )

        # 决策：失败即停，带着已完成结果进入总结；不在错误基础上继续烧 token
        if result.status == "failed":
            step_failed = True
            step_error = result.error
            break

    # ---- 3. 总结 ----
    summary_message = _build_summary_message(user_input, plan, results)
    final_answer = ""
    async for event in stream_final_answer(
        client,
        model,
        [*messages, summary_message],
        tracer=tracer,
        should_stop=should_stop,
        targets=targets,
        provider=provider,
        usage=total_usage,
        deadline=deadline,
    ):
        if isinstance(event, StreamDeltaEvent):
            final_answer += event.content
        yield event

    if deadline is not None and perf_counter() >= deadline:
        timed_out = True
    if should_stop is not None and await should_stop():
        stopped = True

    if timed_out:
        status = "timed_out"
    elif stopped:
        status = "stopped"
    elif step_failed:
        status = "failed"
    else:
        status = "completed"

    state = AgentState(
        run_id=tracer.run_id,
        user_input=user_input,
        plan=plan,
        results=results,
        status=status,
        final_answer=final_answer,
        error=step_error if step_failed else None,
        prompt_tokens=total_usage.prompt_tokens,
        completion_tokens=total_usage.completion_tokens,
        total_tokens=total_usage.total_tokens,
    )
    yield state


async def run_agent(
    client,
    model: str,
    user_input: str,
    *,
    messages: list[dict],
    context: dict | None = None,
    tracer=None,
    **kwargs,
) -> AgentState:
    """非流式入口：跑完整轮并把最终状态收集起来（供测试/后续同步接口使用）。"""
    state: AgentState | None = None
    async for event in run_agent_stream(
        client,
        model,
        user_input,
        messages=messages,
        context=context,
        tracer=tracer,
        **kwargs,
    ):
        if isinstance(event, AgentState):
            state = event
    if state is None:
        raise RuntimeError("agent 循环没有产出最终状态")
    return state
