from celery import Celery

from .config import settings

celery_app = Celery(
    "aichat",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "backend.app.services.task.title_tasks",
        "backend.app.rag.tasks",
    ],
)


celery_app.conf.update(task_track_started=True)
