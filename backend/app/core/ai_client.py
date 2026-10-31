"""AI 客户端工厂与用户 AI 设置解析。

供聊天、标题生成、记忆提取、Agent 共用，避免这些基础设施职责堆在各业务域里。
"""

try:
    from openai import AsyncOpenAI, OpenAI
except Exception:  # pragma: no cover - optional dependency fallback
    AsyncOpenAI = None
    OpenAI = None

import httpx
from dataclasses import dataclass
from sqlalchemy.orm import Session

from ..user.repository import get_user_settings
from .config import settings


def _llm_timeout() -> httpx.Timeout:
    """LLM 调用的超时：连接单独设短一点，让网络问题快速失败。

    max_retries 统一关掉：重试走 core/retry.py，重试次数、退避、
    错误分类都可观测可测试，而不是藏在 SDK 默认值里。
    """
    return httpx.Timeout(
        settings.llm_request_timeout_seconds,
        connect=settings.llm_connect_timeout_seconds,
    )


def _resolve_provider(api_key=None, provider="deepseek"):
    """解析出 (api_key, base_url, model)。api_key 为 None 表示没有可用 Key。

    用户没存 Key 时是否回落到服务器全局 Key 由
    settings.server_api_key_fallback_enabled 控制（默认不回落）。
    """
    key = api_key
    if not key and settings.server_api_key_fallback_enabled:
        key = settings.deepseek_api_key or settings.openai_api_key
    if not key:
        return None, "", ""

    provider = provider.lower() if provider else "deepseek"
    if provider == "openai":
        return key, settings.openai_base_url, settings.openai_model
    return key, settings.deepseek_base_url, settings.deepseek_model


def get_client(api_key=None, provider="deepseek"):
    """同步客户端：给 Celery 后台任务（记忆提取 / 标题生成）用。

    后台任务不在事件循环上跑，用同步客户端最简单，不需要 async 改造。
    """
    if OpenAI is None:
        return None

    key, base_url, model = _resolve_provider(api_key, provider)
    if not key:
        return None

    return OpenAI(api_key=key, base_url=base_url, timeout=_llm_timeout(), max_retries=0), model


def get_async_client(api_key=None, provider="deepseek"):
    """异步客户端：给 HTTP 请求链路（聊天 / Agent 流式）用。

    用它发起的请求是 ``await async_client.chat.completions.create(...)``，
    等待大模型回包的几十秒里事件循环是空的，可以继续处理别的请求。
    """
    if AsyncOpenAI is None:
        return None

    key, base_url, model = _resolve_provider(api_key, provider)
    if not key:
        return None

    return AsyncOpenAI(api_key=key, base_url=base_url, timeout=_llm_timeout(), max_retries=0), model


def get_user_ai_settings(user_id=None, db: Session | None = None):
    api_key = None
    provider = "deepseek"

    if user_id is not None and db is not None:
        row = get_user_settings(db, user_id)
        if row:
            api_key = row.api_key
            provider = row.provider or "deepseek"

    return api_key, provider


@dataclass(frozen=True)
class ModelTarget:
    """一次调用可用的模型出口：provider + 出口地址 + 模型 + Key 归属。

    is_user_key 决定降级边界：用户的 Key 只能在同一家供应商内换模型，
    系统 Key 才允许跨供应商。
    """

    provider: str
    base_url: str
    model: str
    api_key: str
    is_user_key: bool


def _provider_config(provider: str):
    provider = provider.lower()
    if provider == "openai":
        return settings.openai_base_url, settings.openai_model
    return settings.deepseek_base_url, settings.deepseek_model


def _env_key(provider: str):
    return settings.openai_api_key if provider.lower() == "openai" else settings.deepseek_api_key


def resolve_model_targets(api_key=None, provider="deepseek") -> list[ModelTarget]:
    """解析出主目标 + 可选备用目标的候选列表（按尝试顺序）。

    没有可用 Key 时返回空列表，由调用方给"未配置 Key"的提示。
    """
    provider = (provider or "deepseek").lower()
    base_url, model = _provider_config(provider)
    is_user_key = bool(api_key)
    key = api_key
    if not key and settings.server_api_key_fallback_enabled:
        key = _env_key(provider)
    if not key or not model:
        return []

    targets = [ModelTarget(provider, base_url, model, key, is_user_key)]
    if not settings.llm_fallback_enabled:
        return targets

    if is_user_key:
        # 用户自己的 Key 不跨供应商，只在同 provider 内换模型
        fallback_model = settings.llm_fallback_model
        if fallback_model and fallback_model != model:
            targets.append(ModelTarget(provider, base_url, fallback_model, key, True))
        return targets

    fallback_provider = (
        settings.llm_fallback_provider or ("openai" if provider == "deepseek" else "deepseek")
    ).lower()
    if fallback_provider == provider:
        return targets

    fallback_base_url, default_fallback_model = _provider_config(fallback_provider)
    fallback_model = settings.llm_fallback_model or default_fallback_model
    fallback_key = _env_key(fallback_provider)
    if fallback_key and fallback_model:
        targets.append(
            ModelTarget(
                fallback_provider,
                fallback_base_url,
                fallback_model,
                fallback_key,
                False,
            )
        )
    return targets


def get_async_client_for(target: ModelTarget):
    """按目标创建异步客户端（不读到 Key 时返回 None）。"""
    if AsyncOpenAI is None:
        return None
    return AsyncOpenAI(
        api_key=target.api_key,
        base_url=target.base_url,
        timeout=_llm_timeout(),
        max_retries=0,
    )
