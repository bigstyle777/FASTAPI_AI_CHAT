"""MemoryManager：Agent 与 Memory 系统唯一的交互入口。

Agent 只依赖 extract / apply / retrieve 三个方法，不碰数据库、
不碰 ORM、也不直接调 Embedding。

为什么是 async 但底层是同步的
----------------------------
本项目的数据层是同步 SQLAlchemy（见 core/blocking.py 的说明），
而 Agent 主循环跑在 async 链路上。所以这里保持 async 签名让 Agent
能直接 await，内部用 run_blocking 把同步查询丢回线程池，
避免一次慢查询冻住事件循环。

Celery 后台任务不走这个类，直接调下面同名的同步函数。
"""

import logging
from sqlalchemy.orm import Session

from ..core.blocking import run_blocking
from ..core.config import settings
from ..rag.embedding import embed_texts
from .extractor import extract_memories_for_user
from .repository import (
    create_episodic,
    get_by_key as repo_get_by_key,
    list_missing_embedding,
    set_embedding,
    upsert_semantic,
)
from .retriever import retrieve_memories
from .schemas import MemoryAction, MemoryRead, MemoryType

logger = logging.getLogger(__name__)


def _embed_text(db: Session, user_id: int, text: str) -> list[float] | None:
    """给一段文本算向量。失败返回 None，让调用方把记忆存成"待回填"状态。"""
    if not settings.memory_embedding_enabled:
        return None
    try:
        return embed_texts([text], user_id, db)[0]
    except Exception:
        logger.warning("记忆向量化失败，稍后回填", exc_info=True)
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
    """给上次向量化失败的记忆补向量，返回补上的条数。

    没有向量的记忆检索不到（只剩字面匹配这一条路），所以每次提取任务
    结束后跑一遍，把历史欠账清掉。semantic / episodic 都会补。
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


class MemoryManager:
    """Agent 侧的记忆入口，按 (user_id, session_id) 建一次，跟着会话走。"""

    def __init__(self, db: Session, user_id: int, session_id: int | None = None):
        self.db = db
        self.user_id = user_id
        self.session_id = session_id

    async def extract(self, conversation: list[dict[str, str]]) -> list[MemoryAction]:
        """从一轮对话里提取记忆变更意图，不落库。"""
        return await run_blocking(
            extract_memories_for_user,
            self.db,
            self.user_id,
            conversation,
        )

    async def apply(self, actions: list[MemoryAction]) -> list[MemoryRead]:
        """把提取结果落库。"""
        return await run_blocking(
            apply_actions,
            self.db,
            self.user_id,
            actions,
            self.session_id,
        )

    async def retrieve(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[MemoryRead]:
        """检索与 query 最相关的记忆。top_k 默认取 settings.memory_top_k。"""
        return await run_blocking(
            retrieve_memories,
            self.db,
            query,
            self.user_id,
            top_k,
            self.session_id,
        )

    async def get_by_key(self, key: str) -> MemoryRead | None:
        """精确访问：按 (user_id, key) 取一条 semantic 记忆。

        与 retrieve 完全独立 —— 不经过向量/关键词召回，回答
        "这个 key 到底记了什么"是确定的。语义检索（召回+排序）和
        精确访问（确定性查找）是两条路径，永不合并。
        """
        memory = await run_blocking(repo_get_by_key, self.db, self.user_id, key)
        return MemoryRead.model_validate(memory) if memory is not None else None
