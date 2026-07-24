from datetime import datetime

from sqlalchemy import select

from .models import ChatSession, Message, User, UserSetting


def get_user_by_username(db, username):
    stmt = select(User).where(User.username == username)
    return db.execute(stmt).scalar_one_or_none()


def create_user(db, username, password):
    user = User(username=username, password=password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_session(db, user_id, title):
    now = datetime.now()
    chat_session = ChatSession(
        user_id=user_id,
        title=title,
        created_at=now,
        updated_at=now,
    )
    db.add(chat_session)
    db.commit()
    db.refresh(chat_session)
    return chat_session


def create_message(db, session_id, role, content):
    message = Message(
        session_id=session_id,
        role=role,
        content=content,
        created_at=datetime.now(),
    )
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def update_session(db, session_id, last_message):
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        return None

    session.last_message = last_message
    session.updated_at = datetime.now()
    db.commit()
    db.refresh(session)
    return session


def get_sessions_by_user(db, user_id):
    stmt = (
        select(ChatSession)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.is_deleted.is_(False),
        )
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
    )
    return db.execute(stmt).scalars().all()


def get_session_by_user(db, session_id, user_id):
    stmt = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == user_id,
        ChatSession.is_deleted.is_(False),
    )
    return db.execute(stmt).scalar_one_or_none()


def get_messages_by_session(db, session_id):
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.asc())
    )
    return db.execute(stmt).scalars().all()


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
