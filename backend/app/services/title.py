from sqlalchemy.orm import Session

from ..models import ChatSession
from .llm import generate_title

DEFAULT_SESSION_TITLE = "新会话"


def generate_session_title(
    db: Session, session_id: int, message: str, user_id: int
) -> str | None:
    chat_session = db.get(ChatSession, session_id)
    if not chat_session:
        return None

    if chat_session.title != DEFAULT_SESSION_TITLE:
        return chat_session.title

    title = generate_title(message, user_id=user_id, db=db).strip()
    if not title:
        return chat_session.title

    chat_session.title = title
    db.commit()
    db.refresh(chat_session)
    return title
