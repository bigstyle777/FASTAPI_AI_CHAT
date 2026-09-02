from datetime import datetime

from sqlalchemy import delete, select

from ..models import ChatSession, Message


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


def get_last_message_by_session(db, session_id):
    stmt = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()