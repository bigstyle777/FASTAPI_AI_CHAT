from ..crud import get_messages_by_session
from .cache import get_chat_context, set_chat_context


def load_chat_context(db, session_id: int, user_message: str) -> list[dict[str, str]]:
    messages = get_chat_context(session_id)
    if messages is not None:
        messages.append({"role": "user", "content": user_message})
        return messages

    history = get_messages_by_session(db, session_id)
    return [{"role": item.role, "content": item.content} for item in history]


def save_chat_context(session_id: int, messages: list[dict[str, str]]) -> None:
    set_chat_context(session_id, messages[-20:])
