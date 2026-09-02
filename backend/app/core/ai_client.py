"""AI 客户端工厂与用户 AI 设置解析。

供聊天、标题生成、记忆提取、Agent 共用，避免这些基础设施职责堆在各业务域里。
"""

try:
    from openai import AsyncOpenAI, OpenAI
except Exception:  # pragma: no cover - optional dependency fallback
    AsyncOpenAI = None
    OpenAI = None

from sqlalchemy.orm import Session

from ..user.repository import get_user_settings
from .config import settings


def _resolve_provider(api_key=None, provider="deepseek"):
    """解析出 (api_key, base_url, model)。api_key 为 None 表示没有可用 Key。"""
    key = api_key or settings.deepseek_api_key or settings.openai_api_key
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

    return OpenAI(api_key=key, base_url=base_url), model


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

    return AsyncOpenAI(api_key=key, base_url=base_url), model


def get_user_ai_settings(user_id=None, db: Session | None = None):
    api_key = None
    provider = "deepseek"

    if user_id is not None and db is not None:
        row = get_user_settings(db, user_id)
        if row:
            api_key = row.api_key
            provider = row.provider or "deepseek"

    return api_key, provider
