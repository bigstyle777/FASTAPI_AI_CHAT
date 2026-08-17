from celery import Celery

from .config import settings

# include 前缀跟随本模块的实际导入路径（app.* 或 backend.app.*），
# 保证 worker 无论从项目根目录还是 backend/ 目录启动都能正确导入任务模块。
_PACKAGE_ROOT = __package__.rsplit(".core", 1)[0]

celery_app = Celery(
    "aichat",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        f"{_PACKAGE_ROOT}.services.task.title_tasks",
        f"{_PACKAGE_ROOT}.services.task.memory_tasks",
        f"{_PACKAGE_ROOT}.rag.tasks",
    ],
)


celery_app.conf.update(task_track_started=True)
