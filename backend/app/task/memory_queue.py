"""Memory 后台任务的入队封装：入队失败不影响聊天主流程。

Memory Phase 1-3 MVP：异步管线只剩"对话结束后提取 + 落库"这一条，
向量化在 manager.apply_actions 里同步算（详见 manager.py），
回填由同一个 Celery 任务顺便处理。
提取输入只给"最近几条上下文 + 本轮"，不再把整段历史喂进去
（见 memory/extractor.py 的 build_extraction_input）。
Phase 5：新增手动触发的 episodic 合并任务入队封装。
Phase 6：新增生命周期清扫（过期/降级/归档）任务入队封装。
均无定时器，由记忆页按钮按需触发。
"""

import logging

logger = logging.getLogger(__name__)


def _enqueue(task, args: tuple, label: str) -> bool:
    """把任务放进 Celery 队列；入队失败只记日志，不抛给主流程。

    所有 memory 入队都不重试（retry=False）：重复执行会重复提取/合并，
    而这些操作不是幂等的代价较高，宁可丢一次也不要重放。
    """
    try:
        task.apply_async(args=args, retry=False)
        return True
    except Exception:
        logger.exception("Failed to enqueue %s", label)
        return False


def enqueue_memory_extraction(
    user_id: int,
    session_id: int | None,
    conversation: list[dict[str, str]],
    focus_last: int = 2,
) -> bool:
    """把"提取记忆 + 落库 + 回填向量"任务放进 Celery 队列。

    conversation 请由 memory.extractor.build_extraction_input 组装（最后两条
    是本轮 user/assistant），focus_last 默认 2 与之配套：提取只针对本轮，
    前面的上下文仅用于消解指代。
    """
    from .memory_tasks import extract_and_save_memory_task

    return _enqueue(
        extract_and_save_memory_task,
        (user_id, session_id, conversation, focus_last),
        "memory extraction",
    )


def enqueue_consolidation(user_id: int) -> bool:
    """把"合并重复 episodic"任务放进 Celery 队列（记忆页手动触发）。

    合并每组要调一次 LLM，比较慢，所以走队列而不是同步执行。
    """
    from .memory_tasks import consolidate_memories_task

    return _enqueue(consolidate_memories_task, (user_id,), "memory consolidation")


def enqueue_decay(user_id: int) -> bool:
    """把"生命周期清扫（过期/降级/归档）"任务放进 Celery 队列。"""
    from .memory_tasks import decay_memories_task

    return _enqueue(decay_memories_task, (user_id,), "memory decay")
