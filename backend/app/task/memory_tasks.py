"""Memory 相关的 Celery 后台任务。"""

import logging

from ..core.celery_worker import celery_app
from ..core.database import SessionLocal
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
