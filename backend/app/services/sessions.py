from sqlalchemy.orm import Session
from .cache import delete_chat_context
from ..crud import (
    create_session,
    delete_empty_sessions_by_user,
    delete_messages_by_session,
    delete_session,
    get_messages_by_session,
    get_session_by_user,
    get_sessions_by_user,
    rename_session,
    session_has_messages,
    update_session_name,
)
from ..exceptions import BusinessError


def _format_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds")


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


def update_session_name_service(db: Session, user, session_id, new_name: str):
    session = get_session_by_user(db, session_id, user_id=user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    title = (new_name or "").strip()
    if not title:
        raise BusinessError("会话名称不能为空")

    chat_session = update_session_name(db, session.id, title)
    if not chat_session:
        raise BusinessError("会话不存在或已删除")

    return {
        "success": True,
        "session_id": chat_session.id,
        "title": chat_session.title,
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
