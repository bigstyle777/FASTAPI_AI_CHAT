"""总结器：根据计划与各步骤结果流式生成最终答案。"""

from time import perf_counter
from typing import AsyncGenerator

from ..chat.schemas import StreamDeltaEvent, StreamUsageEvent, TokenUsage
from ..core.llm_gateway import stream_completion
from .prompts import FINALIZER_SYSTEM_PROMPT
from .trace import NullTracer
from .usage import accumulate_usage, snapshot_usage


async def _chunks(
    client,
    model: str,
    payload: list[dict],
    targets,
):
    """统一两种来源：目标链流式（可降级）或裸 client（兼容旧调用）。"""
    if targets:
        async for chunk, target in stream_completion(
            targets,
            messages=payload,
            stream_options={"include_usage": True},
        ):
            yield chunk, target
        return

    response = await client.chat.completions.create(
        model=model,
        messages=payload,
        stream=True,
        stream_options={"include_usage": True},
    )
    async for chunk in response:
        yield chunk, None


async def stream_final_answer(
    client,
    model: str,
    messages: list[dict],
    *,
    tracer=None,
    should_stop=None,
    targets=None,
    provider: str | None = None,
    usage: TokenUsage | None = None,
    deadline: float | None = None,
) -> AsyncGenerator[StreamDeltaEvent | StreamUsageEvent, None]:
    """产出最终回答；usage 是"本轮累计"（规划 + 执行 + 总结），不是仅总结段。"""
    if tracer is None:
        tracer = NullTracer()
    total = usage if usage is not None else TokenUsage()
    used_model = model
    used_provider = provider
    system_message = {"role": "system", "content": FINALIZER_SYSTEM_PROMPT}
    payload = [system_message, *messages]

    async with tracer.span(
        "finalize",
        "finalizer",
        input_data={"messages": payload},
    ) as record:
        async for chunk, target in _chunks(client, model, payload, targets):
            if target is not None:
                used_model = target.model
                used_provider = target.provider
            if deadline is not None and perf_counter() >= deadline:
                break
            if should_stop is not None and await should_stop():
                break

            usage_chunk = getattr(chunk, "usage", None)
            if usage_chunk:
                accumulate_usage(
                    total,
                    usage_chunk,
                    model=used_model,
                    provider=used_provider,
                )
                continue
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield StreamDeltaEvent(content=content)

        record.set(
            {
                "prompt_tokens": total.prompt_tokens,
                "completion_tokens": total.completion_tokens,
                "total_tokens": total.total_tokens,
                "model": used_model,
                "provider": used_provider,
            }
        )
        yield StreamUsageEvent(
            usage=snapshot_usage(total).model_copy(
                update={"model": used_model, "provider": used_provider}
            )
        )
