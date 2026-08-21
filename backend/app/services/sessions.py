from sqlalchemy.orm import Session

from ..crud import (
    create_session,
    delete_empty_sessions_by_user,
    delete_messages_by_session,
    delete_session,
    get_session_by_user,
    get_sessions_by_user,
    session_has_messages,
    update_session,
)
from ..exceptions import BusinessError
from .cache import delete_chat_context
from .constants import DEFAULT_SESSION_TITLE


def _format_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds")


def create_session_service(db, user, request):
    title = (request.title or "").strip() or DEFAULT_SESSION_TITLE
    chat_session = create_session(db, user["user_id"], title)
    return {"success": True, "session_id": chat_session.id}


def update_session_service(db: Session, user, session_id: int, request):
    session = get_session_by_user(db, session_id, user_id=user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    title = None
    if request.title is not None:
        title = request.title.strip()
        if not title:
            raise BusinessError("会话名称不能为空")

    if title is None and request.is_pinned is None:
        raise BusinessError("没有需要更新的字段")

    chat_session = update_session(
        db,
        session.id,
        title=title,
        is_pinned=request.is_pinned,
        user_id=user["user_id"],
    )

    if not chat_session:
        raise BusinessError("会话不存在或已删除")

    return {
        "success": True,
        "session_id": chat_session.id,
        "title": chat_session.title,
        "is_pinned": chat_session.is_pinned,
    }


def delete_session_service(db, user, session_id):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    delete_session(db, session_id)
    delete_chat_context(session_id)
    return {"success": True, "message": "会话已删除"}


def clear_session_messages_service(db, user, session_id):
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
                "is_pinned": session.is_pinned,
                "created_at": _format_dt(session.created_at),
                "updated_at": _format_dt(session.updated_at),
            }
        )
    return {"success": True, "sessions": result}
