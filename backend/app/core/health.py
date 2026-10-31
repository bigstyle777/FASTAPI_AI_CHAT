"""健康检查路由：liveness 只看进程，readiness 分级看依赖。

DB / Redis 是关键路径，失败返回 503 让编排层摘流；
Celery 队列积压、MCP 连接异常只标记 degraded 并保持 200，
避免边缘组件抖动误伤本来可用的 API。
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..mcp import mcp_manager
from .config import settings
from .database import get_db
from .redis import get_redis

router = APIRouter(tags=["Ops"])

_DETAIL_MAX_CHARS = 200


def _safe_detail(ok: bool, error: str | None) -> str:
    """失败详情对外收敛：默认只说 failed，不回显异常原文。

    异常字符串可能携带连接串、主机名、内部路径（如 psycopg 的
    ConnectionTimeout 带 host:port）。排障需要原文时设
    HEALTH_EXPOSE_DETAILS=true。
    """
    if ok:
        return "ok"
    if settings.health_expose_details and error:
        return str(error)[:_DETAIL_MAX_CHARS]
    return "failed"


def _check_database(db: Session) -> tuple[bool, str]:
    try:
        db.execute(text("SELECT 1"))
        return True, "ok"
    except Exception as error:  # noqa: BLE001
        return False, _safe_detail(False, str(error))


def _check_redis() -> tuple[bool, str]:
    try:
        get_redis().ping()
        return True, "ok"
    except Exception as error:  # noqa: BLE001
        return False, _safe_detail(False, str(error))


def _check_celery() -> tuple[str, int | None]:
    """队列长度只用于降级判定，Redis 不可用时返回 unknown。"""
    try:
        length = int(get_redis().llen("celery"))
    except Exception:  # noqa: BLE001
        return "unknown", None
    if length > settings.celery_queue_backlog_threshold:
        return "degraded", length
    return "ok", length


@router.get("/health/live")
def health_live():
    return {"status": "ok"}


@router.get("/health/ready")
def health_ready(db: Session = Depends(get_db)):
    db_ok, db_detail = _check_database(db)
    redis_ok, redis_detail = _check_redis()
    celery_status, queue_length = _check_celery()
    unhealthy_mcp = mcp_manager.get_unhealthy_servers()

    critical_ok = db_ok and redis_ok
    degraded = critical_ok and (
        celery_status == "degraded" or bool(unhealthy_mcp)
    )
    if not critical_ok:
        status = "unhealthy"
    elif degraded:
        status = "degraded"
    else:
        status = "ok"

    body = {
        "status": status,
        "checks": {
            "database": {"ok": db_ok, "detail": db_detail},
            "redis": {"ok": redis_ok, "detail": redis_detail},
            "celery": {"status": celery_status, "queue_length": queue_length},
            "mcp": {"unhealthy_servers": unhealthy_mcp},
        },
    }
    return JSONResponse(content=body, status_code=200 if critical_ok else 503)
