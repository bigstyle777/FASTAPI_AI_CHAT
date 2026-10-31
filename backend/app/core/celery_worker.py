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
        f"{_PACKAGE_ROOT}.task.title_tasks",
        f"{_PACKAGE_ROOT}.task.memory_tasks",
        f"{_PACKAGE_ROOT}.rag.tasks",
    ],
)


celery_app.conf.update(
    task_track_started=True,
    # 任务执行完才确认：worker 被 SIGTERM/崩溃时任务不会丢，会重新入队
    task_acks_late=True,
    # 一次只预取一个任务：长任务不再被同一 worker 的队列占满，
    # 也让 warm shutdown 的等待时间可控
    worker_prefetch_multiplier=1,
    task_time_limit=settings.celery_task_time_limit_seconds,
    task_soft_time_limit=settings.celery_task_soft_time_limit_seconds,
    # 可见性超时必须大于硬超时，否则长任务会被 broker 重新投递导致重复执行
    broker_transport_options={
        "visibility_timeout": settings.celery_task_time_limit_seconds * 2
    },
)
