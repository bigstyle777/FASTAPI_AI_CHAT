"""总结器：根据计划与各步骤结果流式生成最终答案。"""

from typing import Generator

from ..schemas import StreamDeltaEvent, StreamUsageEvent, TokenUsage
from .prompts import FINALIZER_SYSTEM_PROMPT
from .trace import NullTracer


def stream_final_answer(
    client,
    model: str,
    messages: list[dict],
    *,
    tracer=None,
    should_stop=None,
) -> Generator[StreamDeltaEvent | StreamUsageEvent, None, None]:
    if tracer is None:
        tracer = NullTracer()
    system_message = {"role": "system", "content": FINALIZER_SYSTEM_PROMPT}
    payload = [system_message, *messages]

    with tracer.span(
        "finalize",
        "finalizer",
        input_data={"messages": payload},
    ) as record:
        response = client.chat.completions.create(
            model=model,
            messages=payload,
            stream=True,
            stream_options={"include_usage": True},
        )
        final_usage = None
        for chunk in response:
            if should_stop is not None and should_stop():
                break
            usage = getattr(chunk, "usage", None)
            if usage:
                final_usage = usage
                continue
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None)
            if content:
                yield StreamDeltaEvent(content=content)

        if final_usage is not None:
            record.set(
                {
                    "prompt_tokens": final_usage.prompt_tokens,
                    "completion_tokens": final_usage.completion_tokens,
                    "total_tokens": final_usage.total_tokens,
                }
            )
            yield StreamUsageEvent(
                usage=TokenUsage(
                    model=model,
                    prompt_tokens=final_usage.prompt_tokens,
                    completion_tokens=final_usage.completion_tokens,
                    total_tokens=final_usage.total_tokens,
                )
            )
        else:
            record.set({"usage": None})
