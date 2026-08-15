"""Memory 相关的 Celery 后台任务。"""

import logging

from ...core.celery_worker import celery_app
from ...core.database import SessionLocal
from ...crud import get_memory_by_id
from ..memory_embedding import embed_memory

logger = logging.getLogger(__name__)


@celery_app.task
def embed_memory_task(memory_id: int, user_id: int):
    """后台为单条 memory 生成句子向量并落库。"""
    db = SessionLocal()
    try:
        memory = get_memory_by_id(db, memory_id, user_id)
        if memory is None:
            logger.warning("memory %s 不存在，跳过向量化", memory_id)
            return None
        return embed_memory(db, user_id, memory)
    except Exception:
        logger.exception("memory %s 向量化任务失败", memory_id)
        return False
    finally:
        db.close()


@celery_app.task
def extract_and_save_memory_task(user_id: int, message: str):
    """后台从消息中提取记忆并保存；保存时自动派发向量化任务。"""
    # 延迟导入，避免模块加载时的循环依赖
    from ..memory import extract_and_save_memory

    db = SessionLocal()
    try:
        saved = extract_and_save_memory(db, user_id, message)
        return [memory.id for memory in saved]
    except Exception:
        logger.exception("提取并保存记忆后台任务失败")
        return []
    finally:
        db.close()
