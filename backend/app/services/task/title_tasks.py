from ...celery_worker import celery_app
from ...core.database import SessionLocal
from ..title import generate_session_title


@celery_app.task
def generate_session_title_task(session_id: int, message: str, user_id: int):
    db = SessionLocal()
    try:
        title = generate_session_title(
            db=db, session_id=session_id, message=message, user_id=user_id
        )
        return title
    finally:
        db.close()
