"""日志系统测试。

覆盖：
    configure_logging     root logger 配置（handler / 级别）、可重复调用不叠加 handler
    request_id 上下文     contextvar 注入与隔离
    RequestContextFilter 日志记录自动携带 request_id
    访问日志中间件       method / path / status / duration_ms / request_id 字段齐全，
                         两个请求 request_id 不同（可关联一次提问的全链路日志）

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_logging.py -v
"""

import logging
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.logging import (  # noqa: E402
    RequestContextFilter,
    configure_logging,
    new_request_id,
    request_id_var,
)
import app.main  # noqa: E402


# ---------------------------------------------------------------------------
# configure_logging
# ---------------------------------------------------------------------------


def test_configure_logging_sets_root_level_and_handler():
    configure_logging(level="DEBUG")
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    ), "root 应该挂上 console handler"


def test_configure_logging_is_idempotent():
    configure_logging(level="INFO")
    before = len(logging.getLogger().handlers)
    configure_logging(level="INFO")
    after = len(logging.getLogger().handlers)
    assert before == after, "重复调用不应叠加 handler（否则每条日志输出多份）"


def test_configure_logging_disables_uvicorn_access_duplication():
    """中间件已输出带 request_id 的访问日志，uvicorn.access 不应再重复输出。"""
    import logging as _logging

    configure_logging(level="INFO")
    assert _logging.getLogger("uvicorn.access").propagate is False
    # 启停消息保留（经 root 用统一格式输出）
    assert _logging.getLogger("uvicorn.error").propagate is True
    assert _logging.getLogger("uvicorn").propagate is True


def test_app_loggers_propagate_to_root(caplog):
    configure_logging(level="INFO")
    with caplog.at_level(logging.INFO, logger="app.services.some_module"):
        logging.getLogger("app.services.some_module").info("hello logging")
    assert any("hello logging" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# request_id 上下文
# ---------------------------------------------------------------------------


def test_new_request_id_is_unique_and_short():
    ids = {new_request_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(4 <= len(i) <= 16 for i in ids), "request_id 应保持短小（进日志行）"


def test_request_id_var_isolated_between_tokens():
    token = request_id_var.set("rid-1")
    assert request_id_var.get() == "rid-1"
    request_id_var.reset(token)
    # 默认值应该是占位符而不是抛异常
    assert request_id_var.get() == "-"


def test_filter_injects_request_id_into_record():
    configure_logging(level="INFO")
    request_id_var.set("rid-abc")
    try:
        record = logging.LogRecord(
            name="app.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        RequestContextFilter().filter(record)
        assert record.request_id == "rid-abc"  # noqa: B018
    finally:
        request_id_var.set("-")


# ---------------------------------------------------------------------------
# 访问日志中间件（打真实 app，不触发 lifespan 避免触碰真实 DB）
# ---------------------------------------------------------------------------


def _access_records(caplog):
    return [r for r in caplog.records if r.name.startswith("app.") and "access" in r.getMessage().lower()] or [
        r for r in caplog.records if r.name == "app.main"
    ]


def test_access_log_middleware_logs_request_fields(caplog):
    configure_logging(level="INFO")
    caplog.set_level(logging.INFO)
    client = TestClient(app.main.app)

    response = client.get("/")

    assert response.status_code == 200
    records = [r for r in caplog.records if r.name == "app.main"]
    assert records, "中间件应通过 app.main logger 输出访问日志"
    message = records[-1].getMessage()
    assert "GET" in message and "/" in message
    assert "200" in message
    assert "duration_ms" in message
    assert getattr(records[-1], "request_id", None) not in (None, "", "-"), (
        "访问日志应携带 request_id"
    )


def test_access_log_request_ids_differ_between_requests(caplog):
    configure_logging(level="INFO")
    caplog.set_level(logging.INFO)
    client = TestClient(app.main.app)

    client.get("/")
    client.get("/")

    records = [r for r in caplog.records if r.name == "app.main"]
    assert len(records) >= 2
    ids = [getattr(r, "request_id", None) for r in records[-2:]]
    assert ids[0] != ids[1], "两个请求的 request_id 应不同（才能区分并发日志）"
