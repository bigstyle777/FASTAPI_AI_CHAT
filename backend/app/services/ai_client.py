"""AI 客户端工厂与用户 AI 设置解析。

供聊天、标题生成、记忆提取、Agent 共用，避免这些基础设施职责堆在 llm.py 里。
"""

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency fallback
    OpenAI = None

from sqlalchemy.orm import Session

from ..core.config import settings
from ..crud import get_user_settings


def get_client(api_key=None, provider="deepseek"):
    if OpenAI is None:
        return None

    key = api_key or settings.deepseek_api_key or settings.openai_api_key
    if not key:
        return None

    provider = provider.lower() if provider else "deepseek"

    if provider == "openai":
        base_url = settings.openai_base_url
        model = settings.openai_model
    else:
        base_url = settings.deepseek_base_url
        model = settings.deepseek_model

    return OpenAI(api_key=key, base_url=base_url), model


def get_user_ai_settings(user_id=None, db: Session | None = None):
    api_key = None
    provider = "deepseek"

    if user_id is not None and db is not None:
        row = get_user_settings(db, user_id)
        if row:
            api_key = row.api_key
            provider = row.provider or "deepseek"

    return api_key, provider