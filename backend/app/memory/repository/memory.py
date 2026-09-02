from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import UserMemory, UserMemoryEmbedding


def create_memory(
    db: Session,
    user_id: int,
    content: str,
) -> UserMemory:
    memory = UserMemory(
        user_id=user_id,
        content=content,
    )

    db.add(memory)
    db.commit()
    db.refresh(memory)

    return memory


def get_memories(
    db: Session,
    user_id: int,
) -> list[UserMemory]:
    stmt = (
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.created_at.desc())
    )

    return list(db.execute(stmt).scalars().all())


def get_memory_by_id(
    db: Session,
    memory_id: int,
    user_id: int,
) -> UserMemory | None:
    stmt = select(UserMemory).where(
        UserMemory.id == memory_id,
        UserMemory.user_id == user_id,
    )

    return db.execute(stmt).scalar_one_or_none()


def delete_memory(
    db: Session,
    memory_id: int,
    user_id: int,
) -> bool:
    memory = get_memory_by_id(
        db,
        memory_id,
        user_id,
    )

    if not memory:
        return False

    # 同时清理向量表（user_memory_embeddings），避免残留孤儿向量
    delete_memory_embeddings(db, memory_id)
    db.delete(memory)
    db.commit()

    return True


def delete_memory_embeddings(
    db: Session,
    memory_id: int,
) -> None:
    """删除某条 memory 在向量表里的全部记录。"""
    db.execute(
        delete(UserMemoryEmbedding).where(UserMemoryEmbedding.memory_id == memory_id)
    )
    db.commit()


def update_memory(
    db: Session,
    memory_id: int,
    user_id: int,
    new_content: str,
) -> UserMemory | None:
    """更新某条 memory 的内容（带用户归属校验）。"""
    memory = get_memory_by_id(db, memory_id, user_id)
    if not memory:
        return None

    memory.content = new_content
    memory.updated_at = datetime.now()
    db.commit()
    db.refresh(memory)
    return memory


def get_memories_without_embeddings(
    db: Session,
    user_id: int,
) -> list[UserMemory]:
    """找出还没有向量化的 memory（老数据或上次向量化失败的），用于回填。"""
    stmt = (
        select(UserMemory)
        .outerjoin(
            UserMemoryEmbedding,
            UserMemoryEmbedding.memory_id == UserMemory.id,
        )
        .where(
            UserMemory.user_id == user_id,
            UserMemoryEmbedding.id.is_(None),
        )
        .order_by(UserMemory.created_at.asc())
    )
    return list(db.execute(stmt).scalars().all())


def replace_memory_embeddings(
    db: Session,
    memory_id: int,
    sentences: list[str],
    embeddings: list[list[float]],
    model: str,
    dimension: int,
) -> None:
    """替换某条 memory 的全部句子向量（先删后插，保证与内容一致）。"""
    db.execute(
        delete(UserMemoryEmbedding).where(UserMemoryEmbedding.memory_id == memory_id)
    )
    for index, (sentence, vector) in enumerate(zip(sentences, embeddings)):
        db.add(
            UserMemoryEmbedding(
                memory_id=memory_id,
                sentence_index=index,
                content=sentence,
                model=model,
                dimension=dimension,
                embedding=vector,
            )
        )
    db.commit()