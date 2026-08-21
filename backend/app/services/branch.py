from ..crud import (
    create_message,
    create_session,
    get_message_by_id,
    get_messages_by_session,
    get_session_by_user,
    update_session,
)
from ..exceptions import BusinessError
from .cache import delete_chat_context
from .constants import BRANCH_TITLE_PREFIX
from .message_context import save_chat_context


def create_branch_service(db, user, session_id):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")
    new_session = create_session(
        db, user["user_id"], f"{BRANCH_TITLE_PREFIX}{session.title}", parent_session_id=session_id
    )

    messages = get_messages_by_session(db, session_id)
    for message in messages:
        create_message(
            db=db,
            session_id=new_session.id,
            role=message.role,
            content=message.content,
            model=message.model,
            prompt_tokens=message.prompt_tokens,
            completion_tokens=message.completion_tokens,
            total_tokens=message.total_tokens,
        )

    new_messages = [
        {
            "role": m.role,
            "content": m.content,
        }
        for m in messages
    ]

    save_chat_context(session_id=new_session.id, messages=new_messages)
    return {"success": True, "session_id": new_session.id}


def create_message_branch_service(db, user, message_id: int):
    message = get_message_by_id(db, message_id, user["user_id"])
    if not message:
        raise BusinessError("消息不存在")

    source_session = get_session_by_user(db, message.session_id, user["user_id"])
    if not source_session:
        raise BusinessError("会话不存在或已删除")

    if message.role == "user":
        title_source = message.content.strip() or source_session.title
    else:
        # AI 回复作为分支点时，用同会话中最近一条 user 消息内容作为标题来源
        user_message = next(
            (
                m
                for m in get_messages_by_session(db, source_session.id)
                if m.role == "user" and m.id < message.id
            ),
            None,
        )
        title_source = (
            user_message.content.strip()
            if user_message and user_message.content.strip()
            else source_session.title
        )

    title = f"{BRANCH_TITLE_PREFIX}{title_source[:20]}"
    branch_session = create_session(
        db,
        user["user_id"],
        title,
        parent_session_id=source_session.id,
        branch_from_message_id=message.id,
    )
    update_session(db, branch_session.id, title_source)
    delete_chat_context(branch_session.id)

    return {
        "success": True,
        "session_id": branch_session.id,
        "title": branch_session.title,
    }
