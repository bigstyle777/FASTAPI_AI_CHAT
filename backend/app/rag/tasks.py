from ..core.celery_worker import celery_app
from ..core.database import SessionLocal
from .service import process_document


@celery_app.task(name="app.rag.tasks.process_document_task")
def process_document_task(document_id: int, user_id: int):
    db = SessionLocal()
    try:
        return process_document(db=db, document_id=document_id, user_id=user_id)
    finally:
        db.close()
