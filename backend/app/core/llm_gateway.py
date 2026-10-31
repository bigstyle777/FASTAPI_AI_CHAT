"""Agent 侧的模型调用网关：重试 + 熔断 + 备用目标降级。

聊天链路（chat/services/llm.py）已经有同样的语义，但 Agent 的调用
分散在 planner / executor / finalizer，且每次调用都可能是"单独一次
规划"或"一整轮工具循环"，所以抽成两个入口：

- ``complete()``：非流式一次调用，按目标链依次尝试；
- ``stream_completion()``：流式调用，**只在产出首个 chunk 之前**允许换目标。

工具循环不在这里重放：工具可能已有副作用，失败即停后由总结阶段
（同样走目标链）收尾，避免同一个工具被执行两次。
"""

from dataclasses import dataclass
from typing import Any, AsyncGenerator

from .ai_client import ModelTarget, get_async_client_for
from .retry import create_chat_completion_with_retry, is_switchable_target_error


@dataclass(frozen=True)
class CompletionResult:
    response: Any
    target: ModelTarget


def _circuit_target(target: ModelTarget) -> tuple[str, str]:
    return target.provider, target.base_url


async def complete(
    targets: list[ModelTarget],
    *,
    messages: list[dict],
    **kwargs,
) -> CompletionResult:
    """非流式调用：按目标链尝试，返回实际命中的目标与响应。"""
    if not targets:
        raise RuntimeError("没有可用的模型目标")

    last_error: Exception | None = None
    for index, target in enumerate(targets):
        client = get_async_client_for(target)
        if client is None:
            continue
        try:
            response = await create_chat_completion_with_retry(
                client,
                model=target.model,
                messages=messages,
                circuit_target=_circuit_target(target),
                **kwargs,
            )
            return CompletionResult(response, target)
        except Exception as error:  # noqa: BLE001
            last_error = error
            if not is_switchable_target_error(error) or index == len(targets) - 1:
                raise

    raise last_error or RuntimeError("没有可用的模型目标")


async def stream_completion(
    targets: list[ModelTarget],
    *,
    messages: list[dict],
    **kwargs,
) -> AsyncGenerator[tuple[Any, ModelTarget], None]:
    """流式调用：逐 chunk 产出 (chunk, target)。

    首个 chunk 之前失败可以切下一个目标；一旦有内容产出，再失败就原样
    抛出——重试会让用户看到重复开头，也可能重复计费。
    """
    if not targets:
        raise RuntimeError("没有可用的模型目标")

    for index, target in enumerate(targets):
        client = get_async_client_for(target)
        if client is None:
            continue
        try:
            response = await create_chat_completion_with_retry(
                client,
                model=target.model,
                messages=messages,
                stream=True,
                circuit_target=_circuit_target(target),
                **kwargs,
            )
        except Exception as error:  # noqa: BLE001
            if not is_switchable_target_error(error) or index == len(targets) - 1:
                raise
            continue

        emitted = False
        try:
            async for chunk in response:
                emitted = True
                yield chunk, target
        except Exception as error:  # noqa: BLE001
            if emitted or not is_switchable_target_error(error) or index == len(targets) - 1:
                raise
        else:
            return
