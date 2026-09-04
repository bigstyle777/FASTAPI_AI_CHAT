"""Memory 混合检索：三路取候选 → 合并 → 一次加权排序 → Top K。

不单独做 Reranking 模块：本阶段排序只需要相似度 / 重要性 / 时效性三个
因子的线性加权，一个函数就够，拆出独立模块只会多一层没有收益的间接。
等以后要上交叉编码器重排时再抽。
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from ..core.config import settings
from ..rag.embedding import embed_query
from .repository import (
    keyword_search_semantic,
    list_by_session,
    touch_accessed,
    vector_search,
)
from .schemas import MemoryRead, MemoryType

logger = logging.getLogger(__name__)

# 排序权重。三个因子各自的含义：
#   similarity 这条记忆和当前问题的相关程度
#   importance 这条记忆本身有多重要（提取时由 LLM 打分）
#   recency    这条记忆有多"新"（半衰期衰减）
# 值来自 Settings，可以用环境变量覆盖，调参不用改代码。
SCORE_WEIGHTS = {
    "similarity": settings.memory_weight_similarity,
    "importance": settings.memory_weight_importance,
    "recency": settings.memory_weight_recency,
}

# 只有向量检索能算出真实相似度。另外两路拿不到，按"命中强度"给固定值，
# 强度从高到低：key 被完整命中 > 正文命中关键词 > 只是同一个会话。
FALLBACK_SIMILARITY = {
    "exact_key": 1.0,
    "keyword": 0.6,
    "session": 0.3,
}


@dataclass
class _Candidate:
    """一路检索命中出来的候选，带它自己的相似度来源。"""

    memory: object
    similarity: float
    source: str


def _recency(memory, now: datetime, half_life_days: float) -> float:
    """时效性：按半衰期指数衰减，刚用过的是 1.0，一个半衰期前是 0.5。"""
    anchor = memory.last_accessed_at or memory.created_at
    if anchor is None:
        return 0.5
    age_days = max(0.0, (now - anchor).total_seconds() / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def _score(memory, similarity: float, now: datetime) -> float:
    recency = _recency(memory, now, settings.memory_recency_half_life_days)
    return (
        SCORE_WEIGHTS["similarity"] * similarity
        + SCORE_WEIGHTS["importance"] * memory.importance
        + SCORE_WEIGHTS["recency"] * recency
    )


def _gather_candidates(
    db: Session,
    query: str,
    user_id: int,
    session_id: int | None,
    limit_per_source: int,
) -> dict[int, _Candidate]:
    """多路召回取并集，按 memory id 去重（同一条取相似度最高的那次命中）。

    检索路径是"并且"，不是"或者"——这是本函数最重要的不变量：

      - 字面/关键词匹配（第 2 路）永远执行，不依赖 embedding 是否存在，
        embedding 服务不可用、该条记录向量为 NULL 都拦不住它；
      - 向量匹配（第 1 路）只是**新增**的一条路，且只在记录 embedding
        不为 NULL 时纳入候选（见 repository.vector_search 的 WHERE）。

    两条路并行取回、合并去重后一起进 reranking，谁也别想替代谁。
    将来任何人想把这里"简化"成单通道，先过这条注释再说。
    """
    candidates: dict[int, _Candidate] = {}

    def offer(memory, similarity: float, source: str) -> None:
        existing = candidates.get(memory.id)
        if existing is None or similarity > existing.similarity:
            candidates[memory.id] = _Candidate(memory, similarity, source)

    # 第 1 路：向量检索。semantic + episodic 都在这条路上，
    # 前提是各自 value/summary 有向量（无向量的行被 vector_search 过滤）。
    if settings.memory_embedding_enabled:
        try:
            query_vector = embed_query(query, user_id, db)
            for memory, similarity in vector_search(
                db, user_id, query_vector, limit_per_source
            ):
                offer(memory, similarity, "vector")
        except Exception:
            # 向量化不可用（没配 Key / 服务挂了）不能拖垮整个检索，
            # 另外两路照样能给出结果
            logger.warning("记忆向量检索失败，改用其余两路", exc_info=True)

    # 第 2 路：semantic 的 key / value 文本匹配。
    # 无条件执行 —— semantic 不依赖向量，这是它区别于 episodic 的根本。
    for memory, exact in keyword_search_semantic(db, user_id, query, limit_per_source):
        source = "exact_key" if exact else "keyword"
        offer(memory, FALLBACK_SIMILARITY[source], source)

    # 第 3 路：同一会话里产生的记忆
    if session_id is not None:
        for memory in list_by_session(db, user_id, session_id, limit_per_source):
            offer(memory, FALLBACK_SIMILARITY["session"], "session")

    return candidates


def retrieve_memories(
    db: Session,
    query: str,
    user_id: int,
    top_k: int | None = None,
    session_id: int | None = None,
) -> list[MemoryRead]:
    """检索与 query 最相关的记忆，返回按分数降序的 Top K。

    top_k 默认取 settings.memory_top_k。三条路径各取 top_k 的若干倍候选，
    合并排序后再截断 —— 每路只取 top_k 的话，某一路的好结果会被另两路
    挤掉，合并阶段就没得选了。
    """
    if top_k is None:
        top_k = settings.memory_top_k
    if top_k <= 0:
        return []

    limit_per_source = top_k * settings.memory_candidate_multiplier
    candidates = _gather_candidates(db, query, user_id, session_id, limit_per_source)

    if not candidates:
        return []

    now = datetime.utcnow()
    scored = [
        (candidate.memory, _score(candidate.memory, candidate.similarity, now))
        for candidate in candidates.values()
    ]
    scored.sort(key=lambda item: item[1], reverse=True)

    picked = scored[:top_k]
    touch_accessed(db, [memory.id for memory, _ in picked])

    results: list[MemoryRead] = []
    for memory, score in picked:
        read = MemoryRead.model_validate(memory)
        read.score = round(score, 4)
        results.append(read)
    return results


def build_memory_context(memories: list[MemoryRead]) -> str:
    """把检索到的记忆拼成一段可以塞进 system prompt 的文本。

    按类型分开写：semantic 是"关于用户的事实"，episodic 是"发生过的事"，
    混在一起模型容易分不清哪些是稳定偏好、哪些只是一次性的决策。
    """
    if not memories:
        return ""

    semantic_lines: list[str] = []
    episodic_lines: list[str] = []

    for memory in memories:
        text = memory.content.strip()
        if not text:
            continue
        if memory.type == MemoryType.SEMANTIC:
            label = f"{memory.key}：" if memory.key else ""
            semantic_lines.append(f"- {label}{text}")
        else:
            label = f"[{memory.event_type}] " if memory.event_type else ""
            episodic_lines.append(f"- {label}{text}")

    blocks: list[str] = []
    if semantic_lines:
        blocks.append("关于用户：\n" + "\n".join(semantic_lines))
    if episodic_lines:
        blocks.append("过去发生的事：\n" + "\n".join(episodic_lines))

    return "\n\n".join(blocks)
