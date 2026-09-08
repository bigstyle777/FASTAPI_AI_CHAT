"""Memory 数据访问层：所有 SQL 都收在这里，上层不直接写查询。"""

import re
from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .models import Memory
from .schemas import MemoryType
from .status import ACTIVE, ARCHIVED, CONSOLIDATED, DECAYING, REINFORCED


# ---------------------------------------------------------------------------
# 字面匹配用的文本切分（供 keyword_search_semantic 拼 LIKE 条件）
# ---------------------------------------------------------------------------

# 英文/数字按连续片段切成词
_LATIN_TOKEN = re.compile(r"[a-z0-9_]+")
# 汉字按连续片段切：跨空格不能组词（"我 不 喜欢"里"我不"不是词）
_HAN_RUN = re.compile(r"[\u4e00-\u9fff]+")

# 一条 query 最多切成多少个片段去拼 LIKE。片段越多 SQL 里的 OR 越多，
# 而召回阶段本来就该求全不求准 —— 20 条足够覆盖正常的提问长度，
# 再长也只是在尾部截断（顺序是整串 → 英文词 → 汉字 bigram）。
MAX_LITERAL_TERMS: int = 20


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------


def upsert_semantic(
    db: Session,
    user_id: int,
    key: str,
    value: str,
    *,
    importance: float = 0.5,
    confidence: float = 0.9,
    source: str = "llm_extraction",
    metadata: dict | None = None,
    embedding: list[float] | None = None,
) -> Memory:
    """按 (user_id, key) 更新或新建一条 semantic 记忆。

    为什么 semantic 的 CREATE 也走 upsert
    ------------------------------------
    (user_id, key) 上有部分唯一索引，同一个 key 建两条会直接
    IntegrityError。而"用户换了说法表达同一件事"是很常见的，
    后说的应该覆盖先说的，所以 CREATE 和 UPSERT 一并处理成 upsert。

    为什么 semantic 也要向量
    -----------------------
    key/value 的字面匹配兜底足够，但"我现在在哪工作"召不回
    "已从字节离职"这种换个说法的事实。value 的向量让语义改述也能
    命中；embedding 服务不可用时传 None，检索自动退化到字面匹配，
    所以这里仍然是"不强制"。
    """
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.type == MemoryType.SEMANTIC.value,
        Memory.key == key,
    )
    memory = db.execute(stmt).scalar_one_or_none()

    if memory is None:
        memory = Memory(
            user_id=user_id,
            type=MemoryType.SEMANTIC.value,
            key=key,
            value=value,
            importance=importance,
            confidence=confidence,
            source=source,
            metadata_=metadata,
            embedding=embedding,
        )
        db.add(memory)
    else:
        memory.value = value
        memory.importance = importance
        memory.confidence = confidence
        memory.source = source
        memory.metadata_ = metadata
        if embedding is not None:
            memory.embedding = embedding
        memory.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(memory)
    return memory


def create_episodic(
    db: Session,
    user_id: int,
    summary: str,
    *,
    session_id: int | None = None,
    event_type: str | None = None,
    importance: float = 0.5,
    confidence: float = 0.9,
    source: str = "llm_extraction",
    embedding: list[float] | None = None,
    metadata: dict | None = None,
) -> Memory:
    """新增一条 episodic 记忆。

    episodic 记的是"发生过什么"，同一件事在不同时间发生就是两条，
    没有天然的唯一键，所以不做 upsert，一律新建。
    """
    memory = Memory(
        user_id=user_id,
        session_id=session_id,
        type=MemoryType.EPISODIC.value,
        summary=summary,
        event_type=event_type,
        importance=importance,
        confidence=confidence,
        source=source,
        embedding=embedding,
        metadata_=metadata,
    )
    db.add(memory)
    db.commit()
    db.refresh(memory)
    return memory


def set_embedding(db: Session, memory_id: int, vector: list[float]) -> None:
    """回填向量。向量化失败过的记忆靠这个补上，否则永远检索不到。"""
    memory = db.get(Memory, memory_id)
    if memory is None:
        return
    memory.embedding = vector
    db.commit()


def touch_accessed(db: Session, memory_ids: list[int]) -> None:
    """把被检索命中的记忆标记为"最近用过"，供排序里的时效性因子使用。

    注意：只动 last_accessed_at。access_count 是 Phase 5（Reinforcement）
    的预留列，本次不写。
    """
    if not memory_ids:
        return
    now = datetime.utcnow()
    for memory in db.execute(
        select(Memory).where(Memory.id.in_(memory_ids))
    ).scalars():
        memory.last_accessed_at = now
    db.commit()


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def get_by_id(db: Session, memory_id: int, user_id: int) -> Memory | None:
    """按 id 取一条记忆，带用户归属校验。"""
    stmt = select(Memory).where(
        Memory.id == memory_id,
        Memory.user_id == user_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_by_key(db: Session, user_id: int, key: str) -> Memory | None:
    """semantic 精确访问：按 (user_id, key) 取一条，不依赖 embedding。

    这是与 retrieve()（语义检索）完全独立的一条路径 —— 检索走的是
    "召回+排序"的不确定过程，而这里回答"这个 key 到底记了什么"是
    确定的。任何时候都不要把它们合并成一套逻辑。
    """
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.type == MemoryType.SEMANTIC.value,
        Memory.key == key,
        Memory.status.notin_((ARCHIVED, CONSOLIDATED)),
    )
    return db.execute(stmt).scalar_one_or_none()


def list_by_key(
    db: Session,
    user_id: int,
    keys: list[str] | None = None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Memory]:
    """按 key 过滤 semantic 记忆。keys 为空时列出该用户全部可用 semantic。
    已归档（ARCHIVED）的不再作为"可用记忆"列出。"""
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.type == MemoryType.SEMANTIC.value,
        Memory.status.notin_((ARCHIVED, CONSOLIDATED)),
    )
    if keys:
        stmt = stmt.where(Memory.key.in_(keys))
    stmt = stmt.order_by(Memory.key.asc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def list_by_user(
    db: Session,
    user_id: int,
    *,
    type: MemoryType | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Memory]:
    """记忆页用：列出某用户的全部记忆，最新的在前。"""
    stmt = select(Memory).where(Memory.user_id == user_id)
    if type is not None:
        stmt = stmt.where(Memory.type == type.value)
    stmt = stmt.order_by(Memory.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def update_content(
    db: Session,
    memory_id: int,
    user_id: int,
    content: str,
) -> Memory | None:
    """修改记忆正文。semantic 改 value，episodic 改 summary。"""
    memory = get_by_id(db, memory_id, user_id)
    if memory is None:
        return None

    if memory.type == MemoryType.SEMANTIC.value:
        memory.value = content
    else:
        memory.summary = content

    # 正文变了，旧向量就不再代表这条记忆，清空等下次回填（两种类型都一样）
    memory.embedding = None

    memory.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(memory)
    return memory


def delete_memory(db: Session, memory_id: int, user_id: int) -> bool:
    memory = get_by_id(db, memory_id, user_id)
    if memory is None:
        return False
    db.delete(memory)
    db.commit()
    return True


def list_missing_embedding(
    db: Session,
    user_id: int,
    limit: int = 50,
) -> list[Memory]:
    """找出还没向量的记忆（上次向量化失败的），用于回填。

    semantic 和 episodic 都可能缺向量：前者是 embedding 服务当时不可用，
    后者是被 update_content 清掉的。
    """
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.embedding.is_(None),
        )
        .order_by(Memory.created_at.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


# ---------------------------------------------------------------------------
# 检索用的三路候选
# ---------------------------------------------------------------------------


def vector_search(
    db: Session,
    user_id: int,
    query_vector: list[float],
    limit: int,
    session_id: int | None = None,
) -> list[tuple[Memory, float]]:
    """第 1 路：向量检索（semantic + episodic），按余弦距离升序。

    不按 type 过滤：semantic 的 value 也带向量（见 upsert_semantic 的
    docstring），换个说法问同一件事要靠这条路才召得回。
    返回 (memory, similarity)，similarity = 1 - distance，已夹到 [0, 1]。
    """
    distance = Memory.embedding.cosine_distance(query_vector)
    stmt = (
        select(Memory, distance.label("distance"))
        .where(
            Memory.user_id == user_id,
            Memory.embedding.is_not(None),
            Memory.status.notin_((ARCHIVED, CONSOLIDATED)),
        )
        .order_by(distance.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [(row[0], max(0.0, min(1.0, 1.0 - float(row.distance)))) for row in rows]


def _literal_terms(query: str) -> list[str]:
    """把 query 切成用于字面匹配的小片段：整串 + 英文/数字词 + 汉字 bigram。

    为什么不能只拿整条 query 去 LIKE
    --------------------------------
    记忆里存的是 value="FastAPI"，用户问的是"FastAPI 做后端怎么样"。
    整串不是 value 的子串，但 "fastapi" 是 —— 只比整串的话，用户说得
    越完整反而越查不到，这条兜底路径等于只对关键词查询有效。

    为什么汉字只取相邻二字组合
    -------------------------
    中文没有空格分词，bigram 是让"后端"/"魔法数字"这类词能对上的最小
    手段。不取单字是因为"的""了""是"几乎命中所有中文记忆，会把这一路
    变成全表扫描；单个汉字的查询由"整串"那一项兜住。

    顺序：整串 → 英文词 → 汉字 bigram，截断时优先保住前面的。
    """
    lowered = (query or "").strip().lower()
    if not lowered:
        return []

    terms = [lowered]
    terms.extend(_LATIN_TOKEN.findall(lowered))
    for run in _HAN_RUN.findall(lowered):
        terms.extend(run[index : index + 2] for index in range(len(run) - 1))

    unique: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term not in seen:
            seen.add(term)
            unique.append(term)
    return unique[:MAX_LITERAL_TERMS]


def keyword_search_semantic(
    db: Session,
    user_id: int,
    query: str,
    limit: int,
) -> list[tuple[Memory, bool]]:
    """第 2 路：semantic 的 key / value 文本匹配。

    返回 (memory, exact)：exact 表示 key 被完整命中（比命中正文更可信）。

    **这一条永不依赖 embedding**：向量为 NULL、embedding 服务不可用、
    全局开关 memory_embedding_enabled 关掉时，它仍是 semantic 检索的
    兜底路径。这正是 semantic"默认不强制 embedding"的落点。

    这一路是召回（求全），不是过滤（求准）：命中任意一个片段就入选，
    具体排第几交给 reranker 打分。宁可多召回几条让排序去筛，也不要在
    召回阶段就把"用户换了个说法"的记忆漏掉。
    """
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.type == MemoryType.SEMANTIC.value,
        Memory.status.notin_((ARCHIVED, CONSOLIDATED)),
    )

    lowered = query.strip().lower()
    terms = _literal_terms(lowered)
    if terms:
        clauses = []
        for term in terms:
            pattern = f"%{term}%"
            clauses.append(
                or_(
                    func.lower(Memory.key).like(pattern),
                    func.lower(Memory.value).like(pattern),
                )
            )
        stmt = stmt.where(or_(*clauses))

    stmt = stmt.order_by(Memory.importance.desc()).limit(limit)
    memories = list(db.execute(stmt).scalars().all())

    term_set = set(terms)
    hits: list[tuple[Memory, bool]] = []
    for memory in memories:
        key = (memory.key or "").lower()
        # key 被完整命中 = 整条 query 就是 key，或 query 里有一个片段等于 key
        exact = bool(lowered) and key in term_set
        hits.append((memory, exact))
    return hits


def list_by_session(
    db: Session,
    user_id: int,
    session_id: int,
    limit: int,
) -> list[Memory]:
    """第 3 路：按 session_id 取本会话产生的记忆。

    这一路不看查询相似度，保证"刚才这轮聊过/决定过的事"能被召回 ——
    用户接着上一句问下去时，往往不需要语义相近，而是要上下文连贯。
    """
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.session_id == session_id,
            Memory.status.notin_((ARCHIVED, CONSOLIDATED)),
        )
        .order_by(Memory.created_at.desc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def delete_by_session(db: Session, session_id: int) -> int:
    """删除某会话产生的全部 episodic 记忆（会话被删时清理）。"""
    result = db.execute(
        delete(Memory).where(
            Memory.session_id == session_id,
            Memory.type == MemoryType.EPISODIC.value,
        )
    )
    db.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# Consolidation（Phase 5）
# ---------------------------------------------------------------------------


def list_consolidation_candidates(
    db: Session,
    user_id: int,
    *,
    since: datetime,
    limit: int = 500,
) -> list[Memory]:
    """找出时间窗内可参与合并的 episodic 记忆。

    只收 status=active（已被合并过的 CONSOLIDATED 不重复进组）且
    有向量的行 —— 聚类靠摘要向量算相似度，没向量无从比。
    按创建时间升序返回，保证组内顺序即事件发生顺序。
    """
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.type == MemoryType.EPISODIC.value,
            Memory.status == ACTIVE,
            Memory.created_at >= since,
            Memory.embedding.is_not(None),
        )
        .order_by(Memory.created_at.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def consolidate_episodes(
    db: Session,
    user_id: int,
    source_ids: list[int],
    *,
    summary: str,
    importance: float,
    confidence: float,
    event_type: str | None,
    embedding: list[float] | None = None,
    metadata: dict | None = None,
) -> Memory | None:
    """把一组 episodic 合并成一条高层总结 —— 单事务，要么全成要么全不动。

    1. 校验：所有源记忆必须属于该用户、是 episodic、仍是 active；
       校验不过（例如其中一条已被别的合并抢先处理）返回 None 不落库。
    2. 新建一条 episodic（source="consolidation"），metadata 里带
       source_episode_ids 溯源。
    3. 原 episodes 标记 status=CONSOLIDATED、consolidated_into_id 指向
       新总结。不物理删除 —— 溯源永远保留原文。

    注意：本函数在 consolidator 里先调完 LLM 再进来，摘要与分数都
    是现成的；它只负责"落库这一步"是原子的。
    """
    originals = db.execute(
        select(Memory).where(
            Memory.id.in_(source_ids),
            Memory.user_id == user_id,
            Memory.type == MemoryType.EPISODIC.value,
            Memory.status == ACTIVE,
        )
    ).scalars().all()
    if len(originals) < 2:
        return None

    originals.sort(key=lambda m: m.created_at)
    meta = dict(metadata or {})
    meta.setdefault("source_episode_ids", sorted(m.id for m in originals))

    merged = Memory(
        user_id=user_id,
        type=MemoryType.EPISODIC.value,
        summary=summary,
        event_type=event_type,
        importance=importance,
        confidence=confidence,
        source="consolidation",
        embedding=embedding,
        metadata_=meta,
    )
    db.add(merged)
    db.flush()  # 先拿到 merged.id 才能回填到原文

    for original in originals:
        original.status = CONSOLIDATED
        original.consolidated_into_id = merged.id

    db.commit()
    db.refresh(merged)
    return merged


# ---------------------------------------------------------------------------
# 生命周期选择（Phase 6：Decay / Reinforcement / Archive）
# ---------------------------------------------------------------------------


def list_stale_memories(
    db: Session,
    user_id: int,
    *,
    older_than: datetime,
    max_importance: float,
    limit: int = 1000,
) -> list[Memory]:
    """找出"该进退化观察"的记忆：ACTIVE/REINFORCED、importance 不高于
    阈值、且很久没被访问（anchor = 最近访问时间，没访问过则用创建时间）。

    只挑低重要性的 —— 用户核心目标类的高重要性记忆不该因冷落被降级。
    按冷落程度升序（最久没用的先处理）。
    """
    anchor = func.coalesce(Memory.last_accessed_at, Memory.created_at)
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.status.in_((ACTIVE, REINFORCED)),
            Memory.importance <= max_importance,
            anchor < older_than,
        )
        .order_by(anchor.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def list_decaying_memories(
    db: Session,
    user_id: int,
    *,
    older_than: datetime,
    limit: int = 1000,
) -> list[Memory]:
    """找观察窗已过的 DECAYING 记忆，准备归档。

    进入 DECAYING 时会把 updated_at 记为当时的时刻（见 decay.py），
    所以这里用 updated_at 判断观察是否到期。
    """
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.status == DECAYING,
            Memory.updated_at < older_than,
        )
        .order_by(Memory.updated_at.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def list_expired_memories(
    db: Session,
    user_id: int,
    *,
    now: datetime,
    limit: int = 1000,
) -> list[Memory]:
    """找已过 expires_at 的记忆。这类不管当前状态如何，直接归档。"""
    stmt = (
        select(Memory)
        .where(
            Memory.user_id == user_id,
            Memory.expires_at.is_not(None),
            Memory.expires_at < now,
            Memory.status != ARCHIVED,
        )
        .order_by(Memory.expires_at.asc())
        .limit(limit)
    )
    return list(db.execute(stmt).scalars().all())


def list_memories_to_reinforce(
    db: Session,
    user_id: int,
    memory_ids: list[int],
) -> list[Memory]:
    """reinforce 的目标行：只允许操作"还活着"的记忆。

    已归档/已合并的不再参与强化 —— 强化等于"确认这条有用"，
    对归档记忆做这个动作没有意义。
    """
    if not memory_ids:
        return []
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.id.in_(memory_ids),
        Memory.status.notin_((ARCHIVED, CONSOLIDATED)),
    )
    return list(db.execute(stmt).scalars().all())
