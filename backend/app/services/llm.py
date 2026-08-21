from sqlalchemy.orm import Session

from ..schemas import StreamErrorEvent
from .ai_client import get_client, get_user_ai_settings
from .tool_calling import run_tool_loop, stream_with_tools


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


def chat_with_ai(messages: list, user_id=None, db=None):
    api_key, provider = get_user_ai_settings(user_id=user_id, db=db)
    result = get_client(api_key=api_key, provider=provider)
    if not result:
        return _build_fallback_reply(messages, api_key=api_key)

    client, model = result

    try:
        context = {"db": db, "user_id": user_id}
        # 先跑工具调用循环；没有注册工具或轮数耗尽时 content 为 None
        history, content = run_tool_loop(client, model, messages, context=context)
        if content is not None:
            return content

        # 兜底: 普通对话请求
        response = client.chat.completions.create(
            model=model,
            messages=history,
        )
        content = response.choices[0].message.content
        return content or _build_fallback_reply(messages, api_key=api_key)
    except Exception as error:
        return _build_fallback_reply(messages, api_key=api_key, error=error)


def chat_with_ai_stream(
    messages: list, user_id: int | None = None, db: Session | None = None
):
    # 获取用户个人 AI 设置（个人中心保存的 Key 优先）
    api_key, provider = get_user_ai_settings(user_id=user_id, db=db)
    result = get_client(api_key=api_key, provider=provider)
    if not result:
        yield StreamErrorEvent(message=_build_fallback_reply(messages, api_key=api_key))
        return

    client, model = result

    try:
        context = {"db": db, "user_id": user_id}
        # 流式生成，内部自动处理工具调用（逻辑见 services/tool_calling.py）
        yield from stream_with_tools(client, model, messages, context=context)
    except Exception as error:
        yield StreamErrorEvent(
            message=_build_fallback_reply(messages, api_key=api_key, error=error)
        )