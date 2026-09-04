"""Memory 后台任务的入队封装：入队失败不影响聊天主流程。

Memory Phase 1-3 MVP：异步管线只剩"对话结束后提取 + 落库"这一条，
向量化在 manager.apply_actions 里同步算（详见 manager.py），
回填由同一个 Celery 任务顺便处理。
Phase 5：新增手动触发的 episodic 合并任务入队封装。
Phase 6：新增生命周期清扫（过期/降级/归档）任务入队封装。
均无定时器，按用户决定由上层按需触发。
"""

import logging

logger = logging.getLogger(__name__)


def enqueue_memory_extraction(
    user_id: int,
    session_id: int | None,
    conversation: list[dict[str, str]],
) -> bool:
    """把"提取记忆 + 落库 + 回填向量"任务放进 Celery 队列。"""
    try:
        from .memory_tasks import extract_and_save_memory_task

        extract_and_save_memory_task.apply_async(
            args=(user_id, session_id, conversation),
            retry=False,
        )
        return True
    except Exception:
        logger.exception("Failed to enqueue memory extraction")
        return False


def enqueue_consolidation(user_id: int) -> bool:
    """把"合并重复 episodic"任务放进 Celery 队列（记忆页手动触发）。

    合并每组要调一次 LLM，比较慢，所以走队列而不是同步执行。
    """
    try:
        from .memory_tasks import consolidate_memories_task

        consolidate_memories_task.apply_async(
            args=(user_id,),
            retry=False,
        )
        return True
    except Exception:
        logger.exception("Failed to enqueue memory consolidation")
        return False


def enqueue_decay(user_id: int) -> bool:
    """把"生命周期清扫（过期/降级/归档）"任务放进 Celery 队列。"""
    try:
        from .memory_tasks import decay_memories_task

        decay_memories_task.apply_async(
            args=(user_id,),
            retry=False,
        )
        return True
    except Exception:
        logger.exception("Failed to enqueue memory decay")
        return False
