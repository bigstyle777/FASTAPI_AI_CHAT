"""阶段 4 配置与可靠语义测试（4B / 4C）。

覆盖：
    新增配置默认值
    DB 连接池参数落到 engine
    Celery 可靠性配置（acks_late / 预取 / 超时 / visibility_timeout）
    RAG 任务重试元数据
    记忆任务失败语义（吞异常返回结果并记录 user_id）
    可信代理判定（默认忽略伪造 X-Forwarded-For）

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_ops_config.py -v
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.celery_worker import celery_app  # noqa: E402
from app.core.config import Settings, settings  # noqa: E402
from app.core.database import engine  # noqa: E402
from app.rag.tasks import process_document_task  # noqa: E402
from app.task import memory_tasks  # noqa: E402
from app.user.router import _get_client_ip, _is_trusted_proxy  # noqa: E402


# ---------------------------------------------------------------------------
# 配置默认值
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, expected",
    [
        ("database_pool_size", 10),
        ("database_max_overflow", 20),
        ("database_pool_recycle_seconds", 1800),
        ("database_pool_pre_ping", True),
        ("celery_task_soft_time_limit_seconds", 120),
        ("celery_task_time_limit_seconds", 180),
        ("celery_task_max_retries", 3),
        ("celery_retry_backoff_seconds", 10),
        ("celery_queue_backlog_threshold", 100),
        ("metrics_enabled", True),
        ("log_json", False),
        ("trusted_proxy_ips", ""),
        ("shutdown_grace_seconds", 30),
    ],
)
def test_phase4_config_defaults(field, expected):
    assert Settings.model_fields[field].default == expected


# ---------------------------------------------------------------------------
# DB 连接池
# ---------------------------------------------------------------------------


def test_engine_uses_configured_pool_settings():
    pool = engine.pool
    assert pool.size() == settings.database_pool_size
    assert pool._max_overflow == settings.database_max_overflow
    assert pool._recycle == settings.database_pool_recycle_seconds
    assert pool._pre_ping == settings.database_pool_pre_ping


# ---------------------------------------------------------------------------
# Celery 可靠性配置
# ---------------------------------------------------------------------------


def test_celery_conf_reliability_flags():
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.worker_prefetch_multiplier == 1
    assert conf.task_time_limit == settings.celery_task_time_limit_seconds
    assert conf.task_soft_time_limit == settings.celery_task_soft_time_limit_seconds
    assert (
        conf.broker_transport_options["visibility_timeout"]
        == settings.celery_task_time_limit_seconds * 2
    )
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit


def test_rag_task_has_retry_metadata():
    assert process_document_task.autoretry_for == (Exception,)
    assert process_document_task.max_retries == settings.celery_task_max_retries
    assert process_document_task.retry_backoff == settings.celery_retry_backoff_seconds
    assert process_document_task.retry_jitter is True


# ---------------------------------------------------------------------------
# 记忆任务失败语义
# ---------------------------------------------------------------------------


def test_memory_task_swallows_exception_but_logs_user_id(monkeypatch, caplog):
    class _FakeSession:
        def close(self):
            return None

    def _boom(*args, **kwargs):
        raise RuntimeError("llm failed")

    monkeypatch.setattr(memory_tasks, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(memory_tasks, "extract_memories_for_user", _boom)
    caplog.set_level("ERROR")

    result = memory_tasks.extract_and_save_memory_task(
        user_id=7, session_id=None, conversation=[]
    )

    assert result == {"applied": [], "backfilled": 0}
    assert any("user_id=7" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------------------
# 可信代理
# ---------------------------------------------------------------------------


def _request(client_ip: str | None, forwarded: str | None):
    headers = {}
    if forwarded is not None:
        headers["x-forwarded-for"] = forwarded
    return SimpleNamespace(
        client=SimpleNamespace(host=client_ip) if client_ip else None,
        headers=headers,
    )


def test_forwarded_for_ignored_by_default(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "")
    assert _get_client_ip(_request("1.2.3.4", "9.9.9.9")) == "1.2.3.4"
    assert _is_trusted_proxy("1.2.3.4") is False


def test_forwarded_for_honored_for_whitelisted_proxy(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "1.2.3.4, 5.6.7.8")
    assert _is_trusted_proxy("1.2.3.4") is True
    assert _get_client_ip(_request("1.2.3.4", "9.9.9.9, 8.8.8.8")) == "9.9.9.9"


def test_forwarded_for_honored_when_wildcard(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "*")
    assert _get_client_ip(_request("1.2.3.4", "9.9.9.9")) == "9.9.9.9"


def test_missing_client_still_returns_placeholder(monkeypatch):
    monkeypatch.setattr(settings, "trusted_proxy_ips", "")
    assert _get_client_ip(_request(None, "9.9.9.9")) == "unknown"
