"""Memory 落库与向量回填：Celery 后台任务的同步辅助（方案 A 精简）。

早期版本这里曾有一层 `MemoryManager` 异步门面，作为 Agent 与 Memory 之间
的 Port/Adapter 契约（见曾存在的 app/agent/ports.py）。该门面在运行时从未
被真正的消费者实例化 —— Agent 的衔接点是 chat/services 的 build_memory_messages
与 MCP 工具 search_memory，后台则走 Celery —— 属于超前抽象，已移除。

保留本模块两个纯同步 helper，仅由 task/memory_tasks.py（Celery）调用：
- apply_actions：把 LLM 提取出的动作落库（含 episodic 向量化）；
- backfill_missing_embeddings：给上次向量化失败的记忆补向量。
"""

import logging
from sqlalchemy.orm import Session

from ..core.config import settings
from ..rag.embedding import embed_texts
from .repository import (
    create_episodic,
    list_missing_embedding,
    set_embedding,
    upsert_semantic,
)
from .schemas import MemoryAction, MemoryRead, MemoryType

logger = logging.getLogger(__name__)


def _embed_text(db: Session, user_id: int, text: str) -> list[float] | None:
    """给一段文本算向量。拿不到**真**向量就返回 None，让记忆存成"待回填"。

    allow_fallback=False 是关键：embedding 服务不可用时宁可留 NULL，也不要
    落一个本地哈希伪向量。伪向量不为 NULL，backfill_missing_embeddings 永远
    跳过它，而 vector_search 已经在拿噪声当相似度排序 —— 这是一个不会
    报错、也不会自愈的永久污染。
    """
    if not settings.memory_embedding_enabled:
        return None
    try:
        return embed_texts([text], user_id, db, allow_fallback=False)[0]
    except Exception:
        logger.warning("记忆向量化不可用，稍后回填", exc_info=True)
        return None


def apply_actions(
    db: Session,
    user_id: int,
    actions: list[MemoryAction],
    session_id: int | None = None,
) -> list[MemoryRead]:
    """把提取结果落库。IGNORE 和字段不全的直接跳过。

    episodic 的向量在这里算：本函数主要由 Celery 后台任务调用，
    不在用户等响应的关键路径上，多一次 embedding 请求可以接受。
    """
    applied: list[MemoryRead] = []

    for action in actions:
        if not action.is_usable():
            continue

        if action.type == MemoryType.SEMANTIC:
            memory = upsert_semantic(
                db,
                user_id,
                action.key,
                action.value,
                importance=action.importance,
                confidence=action.confidence,
                # value 也算向量：让"我现在在哪工作"能召回
                # "已从字节离职"这种换了说法的事实（key 匹配仍是主路径）
                embedding=_embed_text(db, user_id, action.value),
            )
        else:
            memory = create_episodic(
                db,
                user_id,
                action.summary,
                session_id=session_id,
                event_type=action.event_type,
                importance=action.importance,
                confidence=action.confidence,
                embedding=_embed_text(db, user_id, action.summary),
            )

        applied.append(MemoryRead.model_validate(memory))

    return applied


def backfill_missing_embeddings(db: Session, user_id: int) -> int:
    """给上次没拿到向量的记忆补向量，返回补上的条数。

    没有向量的记忆语义检索不到（只剩字面匹配与同会话两条路），所以每次
    提取任务结束后跑一遍，把历史欠账清掉。semantic / episodic 都会补。

    只处理 embedding IS NULL 的行：服务不可用时 _embed_text 存的就是 NULL，
    所以新产生的欠账都能被这里看到。旧的伪向量污染行由 alembic 的
    clear_fallback_memory_embeddings 一次性置 NULL，之后同样走这条回填路。
    """
    pending = list_missing_embedding(db, user_id)
    filled = 0

    for memory in pending:
        vector = _embed_text(db, user_id, memory.content)
        if vector is None:
            continue
        set_embedding(db, memory.id, vector)
        filled += 1

    return filled
