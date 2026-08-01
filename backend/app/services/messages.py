import logging

from ..crud import (
    create_message,
    get_messages_by_session,
    get_session_by_user,
    update_session,
)
from ..exceptions import BusinessError
from .cache import check_rate_limit, get_chat_context, set_chat_context
from .llm import chat_with_ai, chat_with_ai_stream
from .title import generate_session_title


logger = logging.getLogger(__name__)


def _update_session_title_from_first_message(db, user, request):
    try:
        generate_session_title(
            db=db,
            session_id=request.session_id,
            message=request.message,
            user_id=user["user_id"],
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to generate session title")


def send_message_service(db, user, request):
    message = request.message.strip()
    if not message:
        raise BusinessError("消息不能为空")

    session = get_session_by_user(db, request.session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    create_message(db, request.session_id, "user", message)
    update_session(db, request.session_id, message)
    _update_session_title_from_first_message(db, user, request)

    history = get_messages_by_session(db, request.session_id)
    messages = [{"role": item.role, "content": item.content} for item in history]
    ai_reply = chat_with_ai(messages=messages, user_id=user["user_id"], db=db)

    create_message(db, request.session_id, "assistant", ai_reply)
    update_session(db, request.session_id, ai_reply)

    return {"success": True}


def get_messages_service(db, user, session_id):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    history = get_messages_by_session(db, session_id)
    messages = [{"role": item.role, "content": item.content} for item in history]
    return {"success": True, "messages": messages}


def send_message_stream_service(db, user, request):
    user_id = user["user_id"]
    allowed = check_rate_limit(
        key=f"rate_limit:chat:{user_id}", limit=500, expire_seconds=60 * 60
    )
    if not allowed:
        yield "请求太频繁,请稍后重试"
        return

    try:
        message = request.message.strip()
        if not message:
            yield "消息不能为空"
            return

        session = get_session_by_user(db, request.session_id, user["user_id"])
        if not session:
            yield "会话不存在"
            return

        create_message(db, request.session_id, "user", message)
        update_session(db, request.session_id, message)
        _update_session_title_from_first_message(db, user, request)

        messages = get_chat_context(request.session_id)
        if messages is None:
            history = get_messages_by_session(db, request.session_id)
            messages = [
                {"role": item.role, "content": item.content} for item in history
            ]
        else:
            messages.append({"role": "user", "content": message})

        ai_reply = ""
        for chunk in chat_with_ai_stream(messages, user_id=user["user_id"], db=db):
            ai_reply += chunk
            yield chunk

        create_message(db, request.session_id, "assistant", ai_reply)
        update_session(db, request.session_id, ai_reply)

        messages.append({"role": "assistant", "content": ai_reply})
        set_chat_context(request.session_id, messages[-20:])

    except Exception as error:
        yield f"Error: {str(error)}"
