from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .models import ChatSession, Message, User, UserSetting


def get_user_by_username(db, username):
    stmt = select(User).where(User.username == username)
    return db.execute(stmt).scalar_one_or_none()


def get_user_by_id(db, user_id):
    stmt = select(User).where(User.id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def create_user(db, username, password):
    user = User(username=username, password=password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


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


def get_message_by_id(db, message_id, user_id):
    stmt = (
        select(Message)
        .join(ChatSession, Message.session_id == ChatSession.id)
        .where(
            Message.id == message_id,
            ChatSession.user_id == user_id,
        )
    )
    return db.execute(stmt).scalar_one_or_none()


def create_message(
    db,
    session_id,
    role,
    content,
    model=None,
    prompt_tokens=0,
    completion_tokens=0,
    total_tokens=0,
    parent_id=None,
):
    message = Message(
        session_id=session_id,
        role=role,
        content=content,
        created_at=datetime.now(),  # noqa: DTZ005
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        parent_id=parent_id,
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def update_message(db, message_id, new_content):
    stmt = select(Message).where(Message.id == message_id)
    message = db.execute(stmt).scalar_one_or_none()
    if not message:
        return None

    message.content = new_content
    message.updated_at = datetime.now()
    db.commit()
    db.refresh(message)
    return message


def delete_messages_after(db, session_id, message_id):
    result = db.execute(
        delete(Message).where(
            Message.session_id == session_id,
            Message.id > message_id,
        )
    )
    db.commit()
    return result.rowcount or 0


def delete_message(db, message_id, user_id):
    stmt = (
        select(Message)
        .join(ChatSession, Message.session_id == ChatSession.id)
        .where(
            Message.id == message_id,
            ChatSession.user_id == user_id,
        )
    )
    message = db.execute(stmt).scalar_one_or_none()
    if not message:
        return None

    db.delete(message)
    db.commit()
    return message


def delete_message_pair(db, message_id, session_id):
    try:
        message = db.execute(
            select(Message).where(
                Message.session_id == session_id,
                Message.id == message_id,
            )
        ).scalar_one_or_none()
        if not message:
            return None

        messages = [message]
        next_message = db.execute(
            select(Message)
            .where(
                Message.session_id == session_id,
                Message.id > message_id,
            )
            .order_by(Message.id.asc())
            .limit(1)
        ).scalar_one_or_none()
        if next_message and next_message.role == "assistant":
            messages.append(next_message)

        for message in messages:
            db.delete(message)
        db.commit()
        return messages
    except:
        db.rollback()
        raise


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


def get_last_message_by_session(db, session_id):
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def get_message_ancestry(db, message_id):
    messages = []
    seen_ids = set()
    current_id = message_id

    while current_id and current_id not in seen_ids:
        seen_ids.add(current_id)
        message = db.get(Message, current_id)
        if not message:
            break
        messages.append(message)
        current_id = message.parent_id

    messages.reverse()
    return messages


def get_messages_up_to(db, session_id, message_id):
    stmt = (
        select(Message)
        .where(
            Message.session_id == session_id,
            Message.id <= message_id,
        )
        .order_by(Message.id.asc())
    )
    return db.execute(stmt).scalars().all()


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


def get_messages_by_session(db, session_id):
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.asc())
    )
    return db.execute(stmt).scalars().all()


def delete_messages_by_session(db, session_id):
    result = db.execute(delete(Message).where(Message.session_id == session_id))
    db.commit()
    return result.rowcount or 0


def update_session_name(
    db: Session, session_id: int, new_name: str
) -> ChatSession | None:
    chat_session = db.get(ChatSession, session_id)
    if not chat_session:
        return None
    chat_session.title = new_name
    chat_session.updated_at = datetime.now()
    db.commit()
    db.refresh(chat_session)
    return chat_session


def get_user_settings(db, user_id):
    stmt = select(UserSetting).where(UserSetting.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def save_user_settings(db, user_id, api_key, provider):
    settings = get_user_settings(db, user_id)
    if settings:
        settings.api_key = api_key
        settings.provider = provider
        settings.updated_at = datetime.now()
    else:
        settings = UserSetting(
            user_id=user_id,
            api_key=api_key,
            provider=provider,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(settings)

    db.commit()
    db.refresh(settings)
    return settings
