from ..crud import (
    get_last_message_by_session,
    get_message_ancestry,
    get_messages_by_session,
    get_messages_up_to,
    get_session_by_id,
)
from .cache import get_chat_context, set_chat_context


def _to_chat_messages(messages) -> list[dict[str, str]]:
    return [{"role": item.role, "content": item.content} for item in messages]


def _load_message_chain(db, message_id: int):
    ancestry = get_message_ancestry(db, message_id)
    if len(ancestry) > 1:
        return ancestry

    if ancestry:
        anchor = ancestry[0]
        return get_messages_up_to(db, anchor.session_id, anchor.id)

    return []


def get_branch_parent_message_id(db, session_id: int) -> int | None:
    last_message = get_last_message_by_session(db, session_id)
    if last_message:
        return last_message.id

    session = get_session_by_id(db, session_id)
    if session and session.branch_from_message_id:
        return session.branch_from_message_id

    return None


def load_visible_messages(db, session_id: int):
    session = get_session_by_id(db, session_id)
    if not session:
        return []

    local_messages = get_messages_by_session(db, session_id)
    if not session.branch_from_message_id:
        return local_messages

    branch_context = _load_message_chain(db, session.branch_from_message_id)
    return [*branch_context, *local_messages]


def load_chat_context(db, session_id: int, user_message: str) -> list[dict[str, str]]:
    messages = get_chat_context(session_id)
    if messages is not None:
        messages.append({"role": "user", "content": user_message})
        return messages

    return _to_chat_messages(load_visible_messages(db, session_id))


def save_chat_context(session_id: int, messages: list[dict[str, str]]) -> None:
    set_chat_context(session_id, messages[-20:])
