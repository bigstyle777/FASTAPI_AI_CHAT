try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency fallback
    OpenAI = None

from ..core.config import settings
from sqlalchemy.orm import Session

def _get_client(api_key=None, provider="deepseek"):
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


def _build_fallback_reply(messages: list, api_key=None, error=None):
    if error:
        return f"AI 调用失败：{type(error).__name__}: {str(error)}"

    if not api_key:
        return (
            "当前 AI 服务暂时不可用，原因是未配置有效的 API Key。"
            "请在个人中心页面输入你的 API Key，然后保存设置。"
        )

    for message in reversed(messages):
        if message.get("role") == "user":
            content = (message.get("content") or "").strip()
            if content:
                return f"当前 AI 服务暂时不可用，我先记下你的问题：{content}"
    return "当前 AI 服务暂时不可用，请稍后再试。"


def _get_user_ai_settings(user_id=None, db=None):
    api_key = None
    provider = "deepseek"

    if user_id is not None and db is not None:
        from ..crud import get_user_settings

        row = get_user_settings(db, user_id)
        if row:
            api_key = row.api_key
            provider = row.provider or "deepseek"

    return api_key, provider


def chat_with_ai(messages: list, user_id=None, db=None):
    api_key, provider = _get_user_ai_settings(user_id=user_id, db=db)
    result = _get_client(api_key=api_key, provider=provider)
    if not result:
        return _build_fallback_reply(messages, api_key=api_key)

    client, model = result

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        content = response.choices[0].message.content
        return content or _build_fallback_reply(messages, api_key=api_key)
    except Exception as error:
        return _build_fallback_reply(messages, api_key=api_key, error=error)


def chat_with_ai_stream(messages: list, user_id:int|None=None, db:Session|None=None):
    api_key, provider = _get_user_ai_settings(user_id=user_id, db=db)
    result = _get_client(api_key=api_key, provider=provider)
    if not result:
        yield _build_fallback_reply(messages, api_key=api_key)
        return

    client, model = result

    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )

        for chunk in response:
            content = chunk.choices[0].delta.content
            if content:
                yield content
    except Exception as error:
        yield _build_fallback_reply(messages, api_key=api_key, error=error)
