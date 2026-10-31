"""RAG 文档处理任务：解析/切分/嵌入任一步失败都有限重试。"""

import logging

from ..core.celery_worker import celery_app
from ..core.config import settings
from ..core.database import SessionLocal
from .service import process_document

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.rag.tasks.process_document_task",
    # 失败多为上游嵌入限流/网络抖动，重试可自愈；process_document 每次
    # 都会把状态重置为 processing，重复执行不会叠加脏数据
    autoretry_for=(Exception,),
    max_retries=settings.celery_task_max_retries,
    retry_backoff=settings.celery_retry_backoff_seconds,
    retry_backoff_max=600,
    retry_jitter=True,
)
def process_document_task(document_id: int, user_id: int):
    logger.info("开始处理 RAG 文档 document_id=%s user_id=%s", document_id, user_id)
    db = SessionLocal()
    try:
        result = process_document(db=db, document_id=document_id, user_id=user_id)
        logger.info("RAG 文档处理完成 document_id=%s", document_id)
        return result
    finally:
        db.close()
