from ..crud import (
    create_message,
    get_messages_by_session,
    get_session_by_user,
    update_session,
)
from ..exceptions import BusinessError
from .cache import check_rate_limit
from .llm import chat_with_ai, chat_with_ai_stream
from .message_context import load_chat_context, save_chat_context
from .title_queue import enqueue_session_title_generation


def _validate_message_request(db, user, request):
    message = request.message.strip()
    if not message:
        raise BusinessError("消息不能为空")

    session = get_session_by_user(db, request.session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    return message


def send_message_service(db, user, request):
    message = _validate_message_request(db, user, request)

    create_message(db, request.session_id, "user", message)
    update_session(db, request.session_id, message)
    enqueue_session_title_generation(request.session_id, message, user["user_id"])

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
        try:
            message = _validate_message_request(db, user, request)
        except BusinessError as error:
            yield str(error.message)
            return

        create_message(db, request.session_id, "user", message)
        update_session(db, request.session_id, message)
        enqueue_session_title_generation(request.session_id, message, user_id)

        messages = load_chat_context(db, request.session_id, message)

        ai_reply = ""
        for chunk in chat_with_ai_stream(messages, user_id=user_id, db=db):
            ai_reply += chunk
            yield chunk

        create_message(db, request.session_id, "assistant", ai_reply)
        update_session(db, request.session_id, ai_reply)

        messages.append({"role": "assistant", "content": ai_reply})
        save_chat_context(request.session_id, messages)

    except Exception as error:
        yield f"Error: {str(error)}"
