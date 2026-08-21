"""消息落库：把用户/AI 消息写入数据库并维护会话摘要。

供普通聊天（services/messages.py）与 Agent（agent/service.py）共用，
避免两处重复实现相同的落库逻辑。
"""

from ..crud import create_message, update_session
from .message_context import get_branch_parent_message_id
from .task.title_queue import enqueue_session_title_generation


def persist_user_message(db, user_id, session_id, message):
    """落库用户消息、更新会话摘要并触发标题生成，返回新建的消息对象。"""
    parent_id = get_branch_parent_message_id(db, session_id)
    user_message = create_message(
        db, session_id, "user", message, parent_id=parent_id
    )
    update_session(db, session_id, message)
    enqueue_session_title_generation(session_id, message, user_id)
    return user_message


def persist_assistant_message(
    db,
    session_id,
    content,
    *,
    model=None,
    prompt_tokens=0,
    completion_tokens=0,
    total_tokens=0,
    parent_id=None,
):
    """落库 AI 回复并更新会话摘要。"""
    create_message(
        db=db,
        session_id=session_id,
        role="assistant",
        content=content,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        parent_id=parent_id,
    )
    update_session(db, session_id, content)