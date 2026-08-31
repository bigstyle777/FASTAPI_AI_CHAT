"""
Memory 向量化 + 检索

把每条 memory 按句子拆分后做 embedding，存进 pgvector，
再像 RAG 一样用余弦相似度检索。没有向量的老数据会在检索时自动回填。
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import settings
from ..crud import get_memories_without_embeddings, replace_memory_embeddings
from ..models import UserMemory, UserMemoryEmbedding
from ..rag.embedding import embed_query, embed_texts, resolve_embedding_model

logger = logging.getLogger(__name__)

# 中英文句子分隔符（标点后跟空白/换行也算分句）
_SENTENCE_RE = re.compile(r"(?<=[。.！？!?；;])\s*|\n+")


def split_sentences(text: str) -> list[str]:
    """把一段文字按句子拆开；拆不出来时整段作为一句。"""
    parts = [part.strip() for part in _SENTENCE_RE.split(text or "") if part.strip()]
    return parts or [text.strip()]


@dataclass(frozen=True)
class MemoryHit:
    memory_id: int
    content: str
    sentence: str
    score: float


def embed_memory(db: Session, user_id: int, memory) -> bool:
    """给单条 memory 生成句子向量并落库，返回是否成功。"""
    sentences = split_sentences(memory.content)
    if not sentences:
        return False

    vectors = embed_texts(sentences, user_id, db)
    model = resolve_embedding_model(user_id, db)
    replace_memory_embeddings(
        db,
        memory.id,
        sentences,
        vectors,
        model,
        settings.rag_embedding_dimension,
    )
    return True


def sync_memory_embeddings(db: Session, user_id: int) -> int:
    """回填还没有向量的 memory（老数据或上次保存时失败的），返回回填条数。"""
    pending = get_memories_without_embeddings(db, user_id)
    embedded = 0

    for memory in pending:
        try:
            if embed_memory(db, user_id, memory):
                embedded += 1
        except Exception:
            logger.warning("memory %s 向量化失败，跳过", memory.id, exc_info=True)

    return embedded


def retrieve_relevant_memories(
    db: Session,
    user_id: int,
    query: str,
    top_k: int = 5,
) -> list[MemoryHit]:
    """向量检索用户记忆；每条 memory 只保留最相关的一句，避免重复占位。"""
    sync_memory_embeddings(db, user_id)

    query_vector = embed_query(query, user_id, db)
    distance = UserMemoryEmbedding.embedding.cosine_distance(query_vector)

    stmt = (
        select(
            UserMemory.id.label("memory_id"),
            UserMemory.content,
            UserMemoryEmbedding.content.label("sentence"),
            distance.label("distance"),
        )
        .join(UserMemoryEmbedding, UserMemoryEmbedding.memory_id == UserMemory.id)
        .where(UserMemory.user_id == user_id)
        .order_by(distance.asc())
        .limit(top_k * 3)  # 多取一些，按 memory 去重后再截断
    )
    rows = db.execute(stmt).all()

    seen: set[int] = set()
    hits: list[MemoryHit] = []
    for row in rows:
        if row.memory_id in seen:
            continue
        seen.add(row.memory_id)
        hits.append(
            MemoryHit(
                memory_id=row.memory_id,
                content=row.content,
                sentence=row.sentence,
                score=max(0.0, 1.0 - float(row.distance)),
            )
        )
        if len(hits) >= top_k:
            break
    return hits
