from celery import Celery

from .core.config import settings

celery_app = Celery(
    "aichat",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["backend.app.services.task.title_tasks"],
)


celery_app.conf.update(task_track_started=True)
