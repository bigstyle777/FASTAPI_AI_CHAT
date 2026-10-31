"""LLM 调用的错误分类与重试。

策略（保守）：
- 只重试"还没吐出任何 token"的阶段，即 create() 建连/等首包失败。
  流式中途断流不重试：重试会让用户看到重复开头、token 重复计费，
  断流异常沿原路径抛给上层，由上层转成错误事件。
- 可重试错误：网络错误（含超时）、429、5xx。
  4xx 业务错误（401/403/400/404）重试无意义，原样抛出。
- 退避：指数增长 + 全抖动 random(0, min(cap, base * 2^attempt))。
  用全抖动而非"固定间隔+抖动"，是为了上游故障时把大量客户端的
  重试时刻摊平，避免同一时刻集体重试撞出新的流量尖峰。
"""

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

from openai import APIConnectionError, InternalServerError, RateLimitError

from .config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")

# APITimeoutError 是 APIConnectionError 的子类，已被涵盖，无需单列
_RETRYABLE_ERRORS = (APIConnectionError, RateLimitError, InternalServerError)


def is_retryable_llm_error(error: Exception) -> bool:
    """网络/超时/429/5xx 值得重试；4xx 业务错误与未知异常不重试。"""
    return isinstance(error, _RETRYABLE_ERRORS)


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int,
    base_seconds: float,
    cap_seconds: float,
) -> T:
    """对一次异步调用做有限次重试（含首次共 max_attempts 次）。

    可重试错误按指数退避+全抖动等待后再试；不可重试错误或次数耗尽
    时把最后一次异常原样抛出。
    """
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as error:
            if not is_retryable_llm_error(error) or attempt == max_attempts - 1:
                raise
            delay = random.uniform(0, min(cap_seconds, base_seconds * 2**attempt))
            logger.warning(
                "LLM 调用失败（第 %d/%d 次），%.2fs 后重试: %s",
                attempt + 1,
                max_attempts,
                delay,
                error,
            )
            await asyncio.sleep(delay)
    raise AssertionError("unreachable: 循环必然以 return 或 raise 结束")


async def create_chat_completion_with_retry(client, **kwargs):
    """包一层 chat.completions.create()：只重试"还没吐出 token"的建连阶段。

    create() 返回之后的流式消费不在此重试（见模块 docstring 的保守策略）。
    """
    return await call_with_retry(
        lambda: client.chat.completions.create(**kwargs),
        max_attempts=settings.llm_retry_max_attempts,
        base_seconds=settings.llm_retry_base_seconds,
        cap_seconds=settings.llm_retry_cap_seconds,
    )
