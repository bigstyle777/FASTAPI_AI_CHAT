from .ai import chat_with_ai, chat_with_ai_stream
from .auth import create_token, hash_password, verify_password
from .crud import (
    create_message,
    create_session,
    create_user,
    get_messages_by_session,
    get_session_by_user,
    get_sessions_by_user,
    get_user_by_username,
    get_user_settings,
    save_user_settings,
    update_session,
)
from .exceptions import BusinessError


def _format_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds")


def register_user(db, request):
    username = request.username.strip()
    password = request.password.strip()
    user = get_user_by_username(db, username)

    if user:
        return {"success": False, "message": "用户名已存在"}

    hashed_password = hash_password(password)
    create_user(db, username, hashed_password)
    return {"success": True, "message": "注册成功"}


def login_user(db, request):
    username = request.username.strip()
    password = request.password.strip()
    user = get_user_by_username(db, username)

    if not user:
        return {"success": False, "message": "用户不存在"}

    if not verify_password(password, user.password):
        return {"success": False, "message": "密码错误"}

    token = create_token({"user_id": user.id, "username": user.username})
    return {"success": True, "access_token": token, "token_type": "bearer"}


def create_session_service(db, user, request):
    title = (request.title or "").strip() or "新会话"
    chat_session = create_session(db, user["user_id"], title)
    return {"success": True, "session_id": chat_session.id}


def send_message_service(db, user, request):
    message = request.message.strip()
    if not message:
        raise BusinessError("消息不能为空")

    session = get_session_by_user(db, request.session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    create_message(db, request.session_id, "user", message)
    update_session(db, request.session_id, message)

    history = get_messages_by_session(db, request.session_id)
    messages = [{"role": item.role, "content": item.content} for item in history]
    ai_reply = chat_with_ai(messages=messages, user_id=user["user_id"], db=db)

    create_message(db, request.session_id, "assistant", ai_reply)
    update_session(db, request.session_id, ai_reply)

    return {"success": True}


def get_sessions_service(db, user):
    sessions = get_sessions_by_user(db, user["user_id"])
    result = []
    for session in sessions:
        result.append(
            {
                "session_id": session.id,
                "title": session.title,
                "last_message": session.last_message,
                "created_at": _format_dt(session.created_at),
                "updated_at": _format_dt(session.updated_at),
            }
        )
    return {"success": True, "sessions": result}


def get_messages_service(db, user, session_id):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    history = get_messages_by_session(db, session_id)
    messages = [{"role": item.role, "content": item.content} for item in history]
    return {"success": True, "messages": messages}


def send_message_stream_service(db, user, request):
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

        history = get_messages_by_session(db, request.session_id)
        messages = [{"role": item.role, "content": item.content} for item in history]

        ai_reply = ""
        for chunk in chat_with_ai_stream(messages, user_id=user["user_id"], db=db):
            ai_reply += chunk
            yield chunk

        create_message(db, request.session_id, "assistant", ai_reply)
        update_session(db, request.session_id, ai_reply)
    except Exception as error:
        yield f"Error: {str(error)}"


def get_settings_service(db, user):
    settings = get_user_settings(db, user["user_id"])
    if not settings:
        return {"success": True, "api_key": None, "provider": "deepseek"}
    return {
        "success": True,
        "api_key": settings.api_key,
        "provider": settings.provider or "deepseek",
    }


def save_settings_service(db, user, request):
    api_key = (request.api_key or "").strip()
    provider = (request.provider or "deepseek").strip().lower() or "deepseek"
    settings = save_user_settings(db, user["user_id"], api_key, provider)
    return {
        "success": True,
        "api_key": settings.api_key,
        "provider": settings.provider,
    }
