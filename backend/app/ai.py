import os
from pathlib import Path

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency fallback
    OpenAI = None


def _load_env_file():
    for env_path in [
        Path(__file__).resolve().parents[1] / ".env",
        Path(__file__).resolve().parents[2] / ".env",
    ]:
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_env_file()


def _get_client(api_key=None, provider="deepseek"):
    if OpenAI is None:
        return None

    key = api_key or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        return None

    provider = provider.lower() if provider else "deepseek"

    if provider == "openai":
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.getenv("AI_MODEL", "gpt-3.5-turbo")
    else:
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        model = os.getenv("AI_MODEL", "deepseek-chat")

    return OpenAI(api_key=key, base_url=base_url), model


def _build_fallback_reply(messages: list, api_key=None):
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


def chat_with_ai(messages: list, user_id=None, db=None):
    api_key = None
    provider = "deepseek"
    if user_id is not None and db is not None:
        from .crud import get_user_settings

        row = get_user_settings(db, user_id)
        if row:
            api_key = row[0]
            provider = row[1] or "deepseek"

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
    except Exception:
        return _build_fallback_reply(messages, api_key=api_key)


def chat_with_ai_stream(messages: list, user_id=None, db=None):
    api_key = None
    provider = "deepseek"
    if user_id is not None and db is not None:
        from .crud import get_user_settings

        row = get_user_settings(db, user_id)
        if row:
            api_key = row[0]
            provider = row[1] or "deepseek"

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
    except Exception:
        yield _build_fallback_reply(messages, api_key=api_key)
