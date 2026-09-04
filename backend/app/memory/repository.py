"""Memory 数据访问层：所有 SQL 都收在这里，上层不直接写查询。"""

from datetime import datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from .models import Memory
from .schemas import MemoryType


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
    """按 key 过滤 semantic 记忆。keys 为空时列出该用户全部 semantic。"""
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.type == MemoryType.SEMANTIC.value,
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
        )
        .order_by(distance.asc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [(row[0], max(0.0, min(1.0, 1.0 - float(row.distance)))) for row in rows]


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
    """
    stmt = select(Memory).where(
        Memory.user_id == user_id,
        Memory.type == MemoryType.SEMANTIC.value,
    )

    lowered = query.strip().lower()
    if lowered:
        pattern = f"%{lowered}%"
        stmt = stmt.where(
            or_(
                func.lower(Memory.key).like(pattern),
                func.lower(Memory.value).like(pattern),
            )
        )

    stmt = stmt.order_by(Memory.importance.desc()).limit(limit)
    memories = list(db.execute(stmt).scalars().all())

    hits: list[tuple[Memory, bool]] = []
    for memory in memories:
        key = (memory.key or "").lower()
        exact = bool(lowered) and key == lowered
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
