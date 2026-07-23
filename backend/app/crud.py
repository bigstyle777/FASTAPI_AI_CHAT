from datetime import datetime

from .models import ChatSession, Message, User, UserSetting


def _format_dt(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return value.isoformat(timespec="seconds")


def get_user_by_username(db, username):
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    return (user.id, user.username, user.password)


def create_user(db, username, password):
    user = User(username=username, password=password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def create_session(db, user_id, title):
    now = datetime.now()
    session = ChatSession(
        user_id=user_id,
        title=title,
        created_at=now,
        updated_at=now,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session.id


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
    return message.id


def update_session(db, session_id, last_message):
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        return False
    session.last_message = last_message
    session.updated_at = datetime.now()
    db.commit()
    return True


def get_sessions_by_user(db, user_id):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user_id, ChatSession.is_deleted.is_(False))
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        .all()
    )
    return [
        (
            session.id,
            session.title,
            session.last_message,
            _format_dt(session.created_at),
            _format_dt(session.updated_at),
        )
        for session in sessions
    ]


def get_session_by_user(db, session_id, user_id):
    session = (
        db.query(ChatSession)
        .filter(
            ChatSession.id == session_id,
            ChatSession.user_id == user_id,
            ChatSession.is_deleted.is_(False),
        )
        .first()
    )
    if not session:
        return None
    return (
        session.id,
        session.user_id,
        session.title,
        _format_dt(session.created_at),
        _format_dt(session.updated_at),
        session.last_message,
        int(session.is_deleted),
    )


def get_messages_by_session(db, session_id):
    messages = (
        db.query(Message)
        .filter(Message.session_id == session_id)
        .order_by(Message.id.asc())
        .all()
    )
    return [(message.role, message.content) for message in messages]


def get_user_settings(db, user_id):
    settings = db.query(UserSetting).filter(UserSetting.user_id == user_id).first()
    if not settings:
        return None
    return (settings.api_key, settings.provider)


def save_user_settings(db, user_id, api_key, provider):
    settings = db.query(UserSetting).filter(UserSetting.user_id == user_id).first()
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
    return True
