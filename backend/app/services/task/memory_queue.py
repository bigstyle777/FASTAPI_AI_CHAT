"""Memory 后台任务的入队封装：入队失败不影响聊天主流程。"""

import logging

logger = logging.getLogger(__name__)


def enqueue_memory_embedding(memory_id: int, user_id: int) -> bool:
    """把单条 memory 的向量化任务放进 Celery 队列。"""
    try:
        from .memory_tasks import embed_memory_task

        embed_memory_task.apply_async(args=(memory_id, user_id), retry=False)
        return True
    except Exception:
        logger.exception("Failed to enqueue memory embedding")
        return False


def enqueue_memory_extraction(user_id: int, message: str) -> bool:
    """把"提取记忆 + 保存"任务放进 Celery 队列（保存时会再派发向量化）。"""
    try:
        from .memory_tasks import extract_and_save_memory_task

        extract_and_save_memory_task.apply_async(
            args=(user_id, message),
            retry=False,
        )
        return True
    except Exception:
        logger.exception("Failed to enqueue memory extraction")
        return False
