"""Memory 相关的 Celery 后台任务。"""

import logging

from ..core.celery_worker import celery_app
from ..core.database import SessionLocal
from ..memory.consolidator import run_consolidation_for_user
from ..memory.extractor import extract_memories_for_user
from ..memory.manager import apply_actions, backfill_missing_embeddings

logger = logging.getLogger(__name__)


@celery_app.task(name="app.task.memory_tasks.extract_and_save_memory_task")
def extract_and_save_memory_task(
    user_id: int,
    session_id: int | None,
    conversation: list[dict[str, str]],
):
    """对话结束后跑一次：LLM 提取 → 落库 → 顺手回填之前失败的向量。

    整个流程在 Celery worker 上跑，与聊天主链路完全解耦，
    失败也不会让用户看到错误（任务返回值有 applied 数组）。
    """
    db = SessionLocal()
    try:
        actions = extract_memories_for_user(db, user_id, conversation)
        applied = apply_actions(db, user_id, actions, session_id)
        backfilled = backfill_missing_embeddings(db, user_id)
        return {
            "applied": [memory.id for memory in applied],
            "backfilled": backfilled,
        }
    except Exception:
        logger.exception("提取并保存记忆后台任务失败")
        return {"applied": [], "backfilled": 0}
    finally:
        db.close()


@celery_app.task(name="app.task.memory_tasks.consolidate_memories_task")
def consolidate_memories_task(user_id: int):
    """手动触发的 episodic 合并：找重复组 → 逐组 LLM 归纳 → 落库。

    按用户决定不引入定时调度（无 Beat），由用户在记忆页点击"合并"
    或运维手动调用入队。失败不重试 —— 重复事件下次再跑即可。
    """
    db = SessionLocal()
    try:
        result = run_consolidation_for_user(db, user_id)
        logger.info(
            "用户 %s 记忆合并完成: %s", user_id, result
        )
        return result
    except Exception:
        logger.exception("用户 %s 记忆合并后台任务失败", user_id)
        return {"consolidated": 0, "reason": "internal_error"}
    finally:
        db.close()
