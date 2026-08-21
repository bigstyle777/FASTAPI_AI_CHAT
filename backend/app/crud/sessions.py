from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ChatSession, Message


def create_session(
    db, user_id, title, parent_session_id=None, branch_from_message_id=None
):
    now = datetime.now()
    chat_session = ChatSession(
        user_id=user_id,
        title=title,
        parent_session_id=parent_session_id,
        branch_from_message_id=branch_from_message_id,
        created_at=now,
        updated_at=now,
    )
    db.add(chat_session)
    db.commit()
    db.refresh(chat_session)
    return chat_session


def update_session(
    db: Session,
    session_id: int,
    last_message: str | None = None,
    title: str | None = None,
    is_pinned: bool | None = None,
    user_id: int | None = None,
) -> ChatSession | None:
    stmt = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.is_deleted.is_(False),
    )
    if user_id is not None:
        stmt = stmt.where(ChatSession.user_id == user_id)
    session = db.execute(stmt).scalar_one_or_none()
    if session is None:
        return None
    if title is not None:
        session.title = title
    if last_message is not None:
        session.last_message = last_message
    if is_pinned is not None:
        session.is_pinned = is_pinned
    session.updated_at = datetime.now()
    db.commit()
    db.refresh(session)
    return session


def delete_session(db, session_id):
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        return None

    db.execute(delete(Message).where(Message.session_id == session_id))
    session.is_deleted = True
    session.last_message = None
    session.updated_at = datetime.now()
    db.commit()
    db.refresh(session)
    return session


def get_session_by_id(db, session_id):
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    return db.execute(stmt).scalar_one_or_none()


def get_sessions_by_user(db, user_id):
    stmt = (
        select(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.is_deleted.is_(False),
        )
        .order_by(
            ChatSession.is_pinned.desc(),
            ChatSession.updated_at.desc(),
            ChatSession.id.desc(),
        )
    )
    return db.execute(stmt).scalars().all()


def get_session_by_user(db, session_id, user_id):
    stmt = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
        ChatSession.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one_or_none()


def session_has_messages(db, session_id):
    stmt = select(Message.id).where(Message.session_id == session_id).limit(1)
    return db.execute(stmt).first() is not None


def delete_empty_sessions_by_user(db, user_id):
    stmt = select(ChatSession).where(
        ChatSession.user_id == user_id,
        ChatSession.is_deleted.is_(False),
    )
    sessions = db.execute(stmt).scalars().all()
    deleted_ids = []
    now = datetime.now()

    for session in sessions:
        if session.branch_from_message_id:
            continue
        if session.is_pinned:
            continue
        if session_has_messages(db, session.id):
            continue
        session.is_deleted = True
        session.last_message = None
        session.updated_at = now
        deleted_ids.append(session.id)

    if deleted_ids:
        db.commit()

    return deleted_ids