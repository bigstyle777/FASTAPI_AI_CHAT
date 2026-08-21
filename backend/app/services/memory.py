"""记忆的增删查服务（memory 路由用）与上下文构建。"""

import logging

from sqlalchemy.orm import Session

from .task.memory_queue import enqueue_memory_embedding

from ..crud import (
    create_memory,
    delete_memory,
    delete_memory_embeddings,
    get_memories,
    update_memory,
)

logger = logging.getLogger(__name__)


def create_memory_service(
    db: Session,
    user_id: int,
    content: str,
):
    content = (content or "").strip()

    if not content:
        raise ValueError("Memory 内容不能为空")

    memory = create_memory(
        db,
        user_id,
        content,
    )

    # 向量化交给 Celery 后台任务；入队失败不阻塞保存，检索时会自动回填
    enqueue_memory_embedding(memory.id, user_id)

    return memory


def get_memories_service(
    db: Session,
    user_id: int,
):
    return get_memories(
        db,
        user_id,
    )


def delete_memory_service(
    db: Session,
    user_id: int,
    memory_id: int,
):
    success = delete_memory(
        db,
        memory_id,
        user_id,
    )

    if not success:
        raise ValueError("Memory 不存在")

    return {
        "success": True,
        "memory_id": memory_id,
    }


def update_memory_service(
    db: Session,
    user_id: int,
    memory_id: int,
    content: str,
):
    content = (content or "").strip()

    if not content:
        raise ValueError("Memory 内容不能为空")

    memory = update_memory(
        db,
        memory_id,
        user_id,
        content,
    )

    if memory is None:
        raise ValueError("Memory 不存在")

    # 内容变了，旧的句子向量作废：先删掉，再交给 Celery 重新向量化
    delete_memory_embeddings(db, memory_id)
    enqueue_memory_embedding(memory_id, user_id)

    return memory


def build_memory_context(
    db,
    user_id: int,
) -> str:
    """把用户全部长期记忆拼成 system 上下文文本。"""
    memories = get_memories(
        db,
        user_id,
    )

    if not memories:
        return ""

    lines = ["用户长期记忆："]

    for memory in memories:
        lines.append(f"- {memory.content}")

    return "\n".join(lines)
