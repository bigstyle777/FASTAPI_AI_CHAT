"""集中日志配置。

所有业务模块继续用 ``logging.getLogger(__name__)`` 取 logger，
本模块负责一次性把 root logger 配置好：

- 统一 console 格式，带 request_id 占位（由 RequestContextFilter 注入），
  一次用户提问能通过 request_id 串起 middleware / service / agent / 工具调用日志；
- 接管 uvicorn 的日志，避免访问日志输出两份；
- 幂等：重复调用不叠加 handler。

Celery worker 默认会接管 root logger（worker_hijack_root_logger），
worker 进程内的 ``logger.exception`` 由 celery 自行格式化输出，无需重复配置。
"""

import logging
import secrets
import sys
from contextvars import ContextVar

# 请求上下文：middleware 在每个请求开始时生成并 set，请求结束 reset。
# ContextVar 在 async 中天然按任务隔离，SSE 流式生成器内也能取到。
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# uvicorn 的 logger（不走 root，需要单独对齐，否则重复输出）：
# - uvicorn / uvicorn.error：启停消息保留，清掉自带 handler 后冒泡到 root 统一格式；
# - uvicorn.access：访问日志已由 main.py 中间件输出（带 request_id / 耗时 / user_id），
#   uvicorn 自己那条无法关联 request_id，直接静默，避免每个请求输出两份访问日志。
_UVICORN_PROPAGATE = {"uvicorn": True, "uvicorn.error": True, "uvicorn.access": False}


def new_request_id() -> str:
    """生成短小的请求 ID（进得了日志行，不做全局唯一保证）。"""
    return secrets.token_hex(4)


class RequestContextFilter(logging.Filter):
    """把 contextvar 里的 request_id 塞进每条日志记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def configure_logging(level: str | None = None) -> None:
    """配置 root logger；重复调用只更新级别，不叠加 handler。"""
    from .config import settings

    resolved_level = (level or settings.log_level).upper()

    root = logging.getLogger()
    root.setLevel(resolved_level)

    if not any(
        isinstance(h, logging.StreamHandler) and getattr(h, "_aichat_handler", False)
        for h in root.handlers
    ):
        handler = logging.StreamHandler(sys.stderr)
        handler._aichat_handler = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        handler.addFilter(RequestContextFilter())
        root.addHandler(handler)

    # uvicorn 的 logger 对齐：清掉自带 handler，按表决定是否冒泡到 root
    for name, propagate in _UVICORN_PROPAGATE.items():
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.setLevel(resolved_level)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = propagate
