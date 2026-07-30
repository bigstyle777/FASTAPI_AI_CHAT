import base64
import random
import uuid
from .cache import get_chat_context,set_chat_context,delete_chat_context,check_rate_limit
from .llm import chat_with_ai, chat_with_ai_stream
from .auth import (
    ACCESS_TOKEN_TTL_SECONDS,
    cache_user,
    create_login_session,
    get_cached_user,
    hash_password,
    revoke_login_session,
    verify_password,
)
from ..core.config import settings
from ..core.redis import (
    redis_delete,
    redis_get,
    redis_get_json,
    redis_set,
    redis_set_json,
)
from ..crud import (
    create_message,
    create_session,
    create_user,
    delete_empty_sessions_by_user,
    delete_messages_by_session,
    delete_session,
    get_user_by_id,
    get_messages_by_session,
    get_session_by_user,
    get_sessions_by_user,
    get_user_by_username,
    get_user_settings,
    rename_session,
    save_user_settings,
    session_has_messages,
    update_session,
)
from ..exceptions import BusinessError

CAPTCHA_TTL_SECONDS = settings.captcha_ttl_seconds
USER_SETTINGS_CACHE_TTL_SECONDS = settings.user_settings_cache_ttl_seconds
CAPTCHA_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_random = random.SystemRandom()


def _captcha_key(captcha_id):
    return f"auth:captcha:{captcha_id}"


def _settings_cache_key(user_id):
    return f"user:settings:{user_id}"


def _create_captcha_code(length=5):
    return "".join(_random.choice(CAPTCHA_ALPHABET) for _ in range(length))


def _create_captcha_image(code):
    text_items = []
    for index, char in enumerate(code):
        x = 18 + index * 23
        y = 34 + _random.randint(-3, 4)
        rotate = _random.randint(-14, 14)
        color = _random.choice(["#111827", "#0f766e", "#1d4ed8", "#7c2d12"])
        text_items.append(
            f'<text x="{x}" y="{y}" transform="rotate({rotate} {x} {y})" fill="{color}">{char}</text>'
        )

    line_items = []
    for _ in range(4):
        x1 = _random.randint(4, 120)
        y1 = _random.randint(8, 42)
        x2 = _random.randint(4, 120)
        y2 = _random.randint(8, 42)
        color = _random.choice(["#94a3b8", "#99f6e4", "#bfdbfe"])
        line_items.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1" opacity="0.7"/>'
        )

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="136" height="48" viewBox="0 0 136 48">'
        '<rect width="136" height="48" rx="8" fill="#f8fafc"/>'
        f'{"".join(line_items)}'
        '<g font-family="Arial, sans-serif" font-size="24" font-weight="700" letter-spacing="2">'
        f'{"".join(text_items)}'
        "</g>"
        "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def create_captcha_service():
    captcha_id = str(uuid.uuid4())
    code = _create_captcha_code()
    redis_set(_captcha_key(captcha_id), code.lower(), ttl=CAPTCHA_TTL_SECONDS)
    return {
        "success": True,
        "captcha_id": captcha_id,
        "image": _create_captcha_image(code),
        "expires_in": CAPTCHA_TTL_SECONDS,
    }


def _verify_captcha(captcha_id, captcha_code):
    if not captcha_id or not captcha_code:
        return False

    key = _captcha_key(captcha_id)
    stored_code = redis_get(key)
    if stored_code is None:
        return False

    if stored_code != captcha_code.strip().lower():
        return False

    redis_delete(key)
    return True


def _cache_settings(user_id, settings):
    redis_set_json(
        _settings_cache_key(user_id),
        settings,
        ttl=USER_SETTINGS_CACHE_TTL_SECONDS,
    )


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
    if not _verify_captcha(request.captcha_id, request.captcha_code):
        return {"success": False, "message": "验证码错误或已过期"}

    username = request.username.strip()
    password = request.password.strip()
    user = get_user_by_username(db, username)

    if not user:
        return {"success": False, "message": "用户不存在"}

    if not verify_password(password, user.password):
        return {"success": False, "message": "密码错误"}

    token = create_login_session(user)
    return {
        "success": True,
        "access_token": token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL_SECONDS,
    }


def logout_user(token):
    revoke_login_session(token)
    return {"success": True, "message": "退出登录成功"}


def get_user_profile_service(db, user):
    cached_user = get_cached_user(user["user_id"])
    if cached_user:
        return {"success": True, **cached_user}

    db_user = get_user_by_id(db, user["user_id"])
    if not db_user:
        raise BusinessError("用户不存在")

    user_info = {"user_id": db_user.id, "username": db_user.username}
    cache_user(user_info)
    return {"success": True, **user_info}


def create_session_service(db, user, request):
    title = (request.title or "").strip() or "新会话"
    chat_session = create_session(db, user["user_id"], title)
    return {"success": True, "session_id": chat_session.id}


def update_session_service(db, user, session_id, request):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    title = (request.title or "").strip()
    if not title:
        raise BusinessError("会话名称不能为空")

    renamed_session = rename_session(db, session_id, title)
    return {
        "success": True,
        "session_id": renamed_session.id,
        "title": renamed_session.title,
    }


def delete_session_service(db, user, session_id):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    delete_session(db, session_id)
    delete_chat_context(session_id)
    return {"success": True, "message": "会话已删除"}


def delete_messages_service(db, user, session_id):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    deleted_count = delete_messages_by_session(db, session_id)
    delete_chat_context(session_id)

    session_deleted = not session_has_messages(db, session_id)
    if session_deleted:
        delete_session(db, session_id)

    return {
        "success": True,
        "deleted_count": deleted_count,
        "session_deleted": session_deleted,
        "message": "对话已清空，会话已自动删除" if session_deleted else "对话已清空",
    }


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
    for session_id in delete_empty_sessions_by_user(db, user["user_id"]):
        delete_chat_context(session_id)

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
    # 限流
    user_id=user["user_id"]
    allowed=check_rate_limit(key=f"rate_limit:chat:{user_id}",limit=500,expire_seconds=60*60)
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

        # 对话缓存
        messages=get_chat_context(request.session_id)
        if messages is None:
            history = get_messages_by_session(db, request.session_id)
            messages = [{"role": item.role, "content": item.content} for item in history]
        else:
            messages.append({"role": "user", "content": message})
        
        ai_reply = ""
        for chunk in chat_with_ai_stream(messages, user_id=user["user_id"], db=db):
            ai_reply += chunk
            yield chunk

        create_message(db, request.session_id, "assistant", ai_reply)
        update_session(db, request.session_id, ai_reply)

        messages.append(
            {"role":"assistant",
             "content":ai_reply}
        )
        set_chat_context(request.session_id,messages[-20:])
    except Exception as error:
        yield f"Error: {str(error)}"


def get_settings_service(db, user):
    cached_settings = redis_get_json(_settings_cache_key(user["user_id"]))
    if cached_settings is not None:
        return {"success": True, **cached_settings}

    settings = get_user_settings(db, user["user_id"])
    if not settings:
        result = {"api_key": None, "provider": "deepseek"}
        _cache_settings(user["user_id"], result)
        return {"success": True, **result}

    result = {
        "api_key": settings.api_key,
        "provider": settings.provider or "deepseek",
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}


def save_settings_service(db, user, request):
    api_key = (request.api_key or "").strip()
    provider = (request.provider or "deepseek").strip().lower() or "deepseek"
    settings = save_user_settings(db, user["user_id"], api_key, provider)
    result = {
        "api_key": settings.api_key,
        "provider": settings.provider,
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}
