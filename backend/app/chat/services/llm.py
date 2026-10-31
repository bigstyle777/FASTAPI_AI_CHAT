import logging

from sqlalchemy.orm import Session

from ...agent.tool_calling import run_tool_loop, stream_with_tools
from ...core.ai_client import (
    ModelTarget,
    get_async_client_for,
    get_user_ai_settings,
    resolve_model_targets,
)
from ...core.blocking import run_blocking
from ...core.circuit_breaker import CircuitOpenError
from ...core.config import settings
from ...core.retry import create_chat_completion_with_retry, is_retryable_llm_error
from ..schemas import StreamErrorEvent

logger = logging.getLogger(__name__)


def _build_fallback_reply(messages: list, api_key=None, error=None):
    if error:
        return f"AI 调用失败：{type(error).__name__}: {str(error)}"

    if not api_key:
        return (
            "当前 AI 服务暂不可用，原因是未配置有效的 API Key。"
            "请先在个人中心页面输入你的 API Key，然后保存设置。"
        )

    for message in reversed(messages):
        if message.get("role") == "user":
            content = (message.get("content") or "").strip()
            if content:
                return f"当前 AI 服务暂不可用，我先记下你的问题：{content}"
    return "当前 AI 服务暂不可用，请稍后再试。"


def _circuit_target(target: ModelTarget) -> tuple[str, str]:
    return target.provider, target.base_url


def _should_try_next(error: Exception) -> bool:
    """只有"上游不可用"类错误才值得换目标；Key/参数错误换谁都一样。"""
    return isinstance(error, CircuitOpenError) or is_retryable_llm_error(error)


async def chat_with_ai(messages: list, user_id=None, db=None):
    api_key, provider = await run_blocking(get_user_ai_settings, user_id=user_id, db=db)
    targets = resolve_model_targets(api_key, provider)
    if not targets:
        return _build_fallback_reply(messages, api_key=api_key)

    context = {"db": db, "user_id": user_id}
    last_error = None

    for index, target in enumerate(targets):
        client = get_async_client_for(target)
        if client is None:
            continue
        try:
            # 先跑工具调用循环；没有注册工具或轮数耗尽时 content 为 None
            history, content = await run_tool_loop(
                client,
                target.model,
                messages,
                context=context,
                circuit_target=_circuit_target(target),
            )
            if content is not None:
                return content

            # 兜底: 普通对话请求（非流式，整体可安全重试）
            kwargs = {"model": target.model, "messages": history}
            if settings.llm_max_output_tokens:
                kwargs["max_tokens"] = settings.llm_max_output_tokens
            response = await create_chat_completion_with_retry(
                client,
                circuit_target=_circuit_target(target),
                **kwargs,
            )
            content = response.choices[0].message.content
            return content or _build_fallback_reply(messages, api_key=api_key)
        except Exception as error:  # noqa: BLE001
            last_error = error
            if not _should_try_next(error) or index == len(targets) - 1:
                return _build_fallback_reply(messages, api_key=api_key, error=error)
            logger.warning(
                "模型降级 fallback: %s/%s 不可用，切换到 %s/%s: %s",
                target.provider,
                target.model,
                targets[index + 1].provider,
                targets[index + 1].model,
                error,
            )

    return _build_fallback_reply(messages, api_key=api_key, error=last_error)


async def chat_with_ai_stream(
    messages: list, user_id: int | None = None, db: Session | None = None
):
    # 获取用户个人 AI 设置（个人中心保存的 Key 优先）
    api_key, provider = await run_blocking(get_user_ai_settings, user_id=user_id, db=db)
    targets = resolve_model_targets(api_key, provider)
    if not targets:
        yield StreamErrorEvent(message=_build_fallback_reply(messages, api_key=api_key))
        return

    context = {"db": db, "user_id": user_id}

    for index, target in enumerate(targets):
        client = get_async_client_for(target)
        if client is None:
            continue

        # 已经吐过 token 就不能再换目标：重放会让用户看到重复开头、重复计费
        emitted = False
        try:
            async for event in stream_with_tools(
                client,
                target.model,
                messages,
                context=context,
                circuit_target=_circuit_target(target),
                provider=target.provider,
            ):
                emitted = True
                yield event
            return
        except Exception as error:  # noqa: BLE001
            if emitted or not _should_try_next(error) or index == len(targets) - 1:
                yield StreamErrorEvent(
                    message=_build_fallback_reply(messages, api_key=api_key, error=error)
                )
                return
            logger.warning(
                "模型降级 fallback: %s/%s 不可用，切换到 %s/%s: %s",
                target.provider,
                target.model,
                targets[index + 1].provider,
                targets[index + 1].model,
                error,
            )
