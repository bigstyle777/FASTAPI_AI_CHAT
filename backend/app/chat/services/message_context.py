from ...core.cache import get_chat_context, set_chat_context
from ...core.config import settings
from ...rag.prompts import build_context_message
from ...rag.retriever import retrieve_relevant_chunks

from ..repository import (
    get_last_message_by_session,
    get_message_ancestry,
    get_messages_by_session,
    get_messages_up_to,
    get_session_by_id,
)
from ...memory.decay import reinforce_memories
from ...memory.retriever import build_memory_context, retrieve_memories


def build_memory_messages(
    db,
    user_id: int,
    query: str,
    session_id: int | None = None,
) -> list[dict[str, str]]:
    """检索相关记忆，拼成可放进 system 的消息列表（可为空）。

    调用方应把本函数返回的列表与历史消息拼起来再喂给 LLM ——
    关键是这些消息**不要**写进 ``save_chat_context`` 的缓存里。
    下次会话可能用相同的 query 重新检索，最新一轮的记忆才能体现；
    把记忆烤进缓存会让它"凝固"，越攒越旧。

    reinforce 触点在"记忆真的被拼进消息、即将喂给模型"这一刻：
    检索命中只是"被看到"（刷新 last_accessed_at），拼进 prompt 才是
    "被用上"，才累加 access_count / 上调 confidence —— 这正是
    agent 运行时与记忆生命周期的唯一衔接点。
    """
    if not settings.memory_enabled:
        return []
    memories = retrieve_memories(db, query, user_id, session_id=session_id)
    text = build_memory_context(memories)
    if not text:
        return []
    # 即将进入模型上下文 → 确认这批记忆被用上了
    reinforce_memories(db, user_id, [memory.id for memory in memories])
    return [{
        "role": "system",
        "content": (
            "以下是关于该用户的记忆，仅供参考；与用户最新说法冲突时"
            "以用户最新说法为准：\n" + text
        ),
    }]


def to_chat_messages(messages) -> list[dict[str, str]]:
    """把 ORM 消息对象压平成 LLM 所需的 role/content 字典列表。"""
    return [{"role": item.role, "content": item.content} for item in messages]


def trim_chat_messages(messages: list[dict]) -> list[dict]:
    """把喂给模型的历史砍到预算内：从最新往回挑，条数或字数用尽就停。

    为什么要砍
    ----------
    历史每多一条，模型生成前要"读"的内容就多一截，首字延迟随之变长，
    而且是随会话长度线性恶化——聊到第 200 条时，光把这个会话重读一遍
    就要好几秒。缓存路径原本只存最近 20 条（见 save_chat_context），
    但缓存过期后回源数据库时是**全量**加载，两条路径行为不一致。

    为什么两个上限都要
    ------------------
    只看条数：20 条可能是 20 句闲聊，也可能是 20 段几千字的长文。
    只看字数：一条超长消息就会把预算吃光。所以先看条数、再看字数。

    注意：最新的那条永远保留（哪怕它自己就超预算），否则用户刚问的
    问题会被丢掉，模型收到的上下文就断了。
    """
    picked: list[dict] = []
    total_chars = 0

    for message in reversed(messages):
        if len(picked) >= settings.chat_context_max_messages:
            break

        length = len(message.get("content") or "")
        # picked 非空才检查字数，保证第一条（最新的）一定被选中
        if picked and total_chars + length > settings.chat_context_max_chars:
            break

        picked.append(message)
        total_chars += length

    picked.reverse()
    return picked


def load_model_context(db, session_id: int) -> list[dict[str, str]]:
    """取喂给模型用的历史：完整可见历史 + 截断到预算内。

    只给模型用。前端展示历史（get_messages_service）和创建分支
    （branch.py）要的是全量，别用这个。
    """
    return trim_chat_messages(to_chat_messages(load_visible_messages(db, session_id)))


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

    return load_model_context(db, session_id)


def save_chat_context(session_id: int, messages: list[dict[str, str]]) -> None:
    # 存进缓存的就是下一轮的上下文，按同一套规则截断，
    # 这样"缓存命中"和"缓存过期回源数据库"两条路径拿到的长度一致
    set_chat_context(session_id, trim_chat_messages(messages))
