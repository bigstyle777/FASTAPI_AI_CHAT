"""阶段 4 运维能力测试（4A / 4C）。

覆盖：
    健康检查分级      /health/live 恒 200；DB/Redis 失败 → 503；
                     Celery 积压、MCP 异常 → 200 degraded
    指标原语          Counter / Gauge / Histogram 累加与 Prometheus 文本渲染
    /metrics 端点     开关、文本响应、HTTP 埋点
    LLM 埋点          成功与失败结果计数、耗时直方图
    JSON 日志         LOG_JSON 时每行合法 JSON 且含 request_id
    生命周期          shutting_down 标志与聊天终态映射

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_health_and_metrics.py -v
"""

import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

import app.main  # noqa: E402
from app.chat.services import messages  # noqa: E402
from app.core import health, lifecycle, metrics  # noqa: E402
from app.core.database import get_db  # noqa: E402
from app.core.logging import JsonFormatter  # noqa: E402
from app.core.retry import create_chat_completion_with_retry  # noqa: E402
from app.core.config import settings  # noqa: E402


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_metrics():
    """每个用例从零开始，避免跨用例累计值干扰断言。"""
    metrics.reset()
    yield
    metrics.reset()


class _FakeDb:
    def __init__(self, fail: bool = False):
        self.fail = fail

    def execute(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("database down")
        return None


class _FakeRedis:
    def __init__(self, queue_length: int = 0, ping_ok: bool = True, llen_ok: bool = True):
        self.queue_length = queue_length
        self.ping_ok = ping_ok
        self.llen_ok = llen_ok

    def ping(self):
        if not self.ping_ok:
            raise RuntimeError("redis down")
        return True

    def llen(self, queue):
        if not self.llen_ok:
            raise RuntimeError("llen failed")
        return self.queue_length


def _patch_dependencies(monkeypatch, *, db_fail=False):
    monkeypatch.setattr(health, "_check_database", lambda db: (not db_fail, "down" if db_fail else "ok"))
    monkeypatch.setattr(health, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(health.mcp_manager, "get_unhealthy_servers", lambda: {})


# ---------------------------------------------------------------------------
# 健康检查分级
# ---------------------------------------------------------------------------


def test_health_live_is_always_ok():
    client = TestClient(app.main.app)
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_ready_ok(monkeypatch):
    monkeypatch.setattr(health, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(health.mcp_manager, "get_unhealthy_servers", lambda: {})
    ready = health.health_ready(db=_FakeDb())
    assert ready.status_code == 200
    body = json.loads(ready.body)
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["redis"]["ok"] is True
    assert body["checks"]["celery"]["status"] == "ok"


def test_health_ready_returns_503_when_database_down():
    ready = health.health_ready(db=_FakeDb(fail=True))
    assert ready.status_code == 503
    body = json.loads(ready.body)
    assert body["status"] == "unhealthy"
    assert body["checks"]["database"]["ok"] is False


def test_health_ready_returns_503_when_redis_down(monkeypatch):
    monkeypatch.setattr(health, "get_redis", lambda: _FakeRedis(ping_ok=False))
    ready = health.health_ready(db=_FakeDb())
    assert ready.status_code == 503
    body = json.loads(ready.body)
    assert body["status"] == "unhealthy"
    assert body["checks"]["redis"]["ok"] is False


def test_health_ready_degraded_on_celery_backlog(monkeypatch):
    monkeypatch.setattr(
        health,
        "get_redis",
        lambda: _FakeRedis(queue_length=settings.celery_queue_backlog_threshold + 1),
    )
    ready = health.health_ready(db=_FakeDb())
    assert ready.status_code == 200
    body = json.loads(ready.body)
    assert body["status"] == "degraded"
    assert body["checks"]["celery"]["status"] == "degraded"


def test_health_ready_degraded_on_mcp_unhealthy(monkeypatch):
    monkeypatch.setattr(health, "get_redis", lambda: _FakeRedis())
    monkeypatch.setattr(
        health.mcp_manager,
        "get_unhealthy_servers",
        lambda: {"search": "connect timeout"},
    )
    ready = health.health_ready(db=_FakeDb())
    assert ready.status_code == 200
    body = json.loads(ready.body)
    assert body["status"] == "degraded"
    assert body["checks"]["mcp"]["unhealthy_servers"] == {"search": "connect timeout"}


def test_health_ready_celery_unknown_keeps_ok(monkeypatch):
    """Redis 抖动导致拿不到队列长度时只标 unknown，不该把服务判成 degraded。"""
    monkeypatch.setattr(
        health, "get_redis", lambda: _FakeRedis(ping_ok=True, llen_ok=False)
    )
    ready = health.health_ready(db=_FakeDb())
    body = json.loads(ready.body)
    assert ready.status_code == 200
    assert body["status"] == "ok"
    assert body["checks"]["celery"]["status"] == "unknown"


def test_health_ready_route_returns_503_when_database_down(monkeypatch):
    """走真实路由 + 依赖注入，确认 HTTP 状态码与函数返回值一致。"""
    _patch_dependencies(monkeypatch, db_fail=True)
    app.main.app.dependency_overrides[get_db] = lambda: _FakeDb(fail=True)
    try:
        client = TestClient(app.main.app)
        response = client.get("/health/ready")
    finally:
        app.main.app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json()["status"] == "unhealthy"


# ---------------------------------------------------------------------------
# 指标原语
# ---------------------------------------------------------------------------


def test_counter_accumulates_per_label_set():
    counter = metrics.Counter("test_counter_total", "测试计数", ("kind",))
    counter.inc({"kind": "a"})
    counter.inc({"kind": "a"})
    counter.inc({"kind": "b"}, 3)
    text = counter._samples()
    assert 'test_counter_total{kind="a"} 2' in text
    assert 'test_counter_total{kind="b"} 3' in text


def test_gauge_inc_and_dec():
    gauge = metrics.Gauge("test_gauge", "测试瞬时值")
    gauge.inc()
    gauge.inc()
    gauge.dec()
    assert gauge._samples() == ["test_gauge 1"]


def test_histogram_buckets_are_cumulative():
    histogram = metrics.Histogram(
        "test_duration_seconds", "测试耗时", buckets=(0.1, 0.5, 1.0)
    )
    histogram.observe(0.05)
    histogram.observe(0.3)
    histogram.observe(2.0)

    samples = "\n".join(histogram._samples())
    assert 'test_duration_seconds_bucket{le="0.1"} 1' in samples
    assert 'test_duration_seconds_bucket{le="0.5"} 2' in samples
    assert 'test_duration_seconds_bucket{le="1"} 2' in samples
    assert 'test_duration_seconds_bucket{le="+Inf"} 3' in samples
    assert "test_duration_seconds_count 3" in samples


def test_render_contains_help_and_type_lines():
    metrics.HTTP_REQUESTS.inc({"method": "GET", "path": "/", "status": "200"})
    text = metrics.render()
    assert "# HELP aichat_http_requests_total" in text
    assert "# TYPE aichat_http_requests_total counter" in text
    assert "# TYPE aichat_http_request_duration_seconds histogram" in text
    assert "# TYPE aichat_sse_active_connections gauge" in text
    assert 'aichat_http_requests_total{method="GET",path="/",status="200"} 1' in text


# ---------------------------------------------------------------------------
# /metrics 端点与 HTTP 埋点
# ---------------------------------------------------------------------------


def test_metrics_endpoint_returns_prometheus_text(monkeypatch):
    # 避免测试环境真连 Redis
    monkeypatch.setattr(app.main, "refresh_queue_length", lambda: None)
    client = TestClient(app.main.app)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    for name in (
        "aichat_http_requests_total",
        "aichat_http_request_duration_seconds",
        "aichat_llm_calls_total",
        "aichat_sse_active_connections",
        "aichat_agent_runs_total",
        "aichat_celery_queue_length",
    ):
        assert name in body


def test_metrics_endpoint_404_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", False)
    monkeypatch.setattr(app.main, "refresh_queue_length", lambda: None)
    response = TestClient(app.main.app).get("/metrics")
    assert response.status_code == 404
    assert response.json()["message"] == "metrics disabled"


def test_http_request_increments_counter_and_histogram():
    client = TestClient(app.main.app)
    client.get("/")

    text = metrics.render()
    assert (
        'aichat_http_requests_total{method="GET",path="/",status="200"} 1' in text
    )
    assert 'aichat_http_request_duration_seconds_count{method="GET",path="/"} 1' in text


def test_route_template_used_as_metric_path(monkeypatch):
    """带路径参数的请求应记成路由模板，避免标签基数爆炸。"""
    monkeypatch.setattr(app.main, "refresh_queue_length", lambda: None)

    @app.main.app.get("/__test_lab/items/{item_id}")
    def _probe(item_id: int):
        return {"item_id": item_id}

    client = TestClient(app.main.app)
    response = client.get("/__test_lab/items/42")
    assert response.status_code == 200

    text = metrics.render()
    assert 'path="/__test_lab/items/{item_id}"' in text
    assert 'path="/__test_lab/items/42"' not in text


# ---------------------------------------------------------------------------
# LLM 埋点
# ---------------------------------------------------------------------------


def test_llm_success_is_counted_with_duration():
    class _Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(id="ok")

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    async def run():
        await create_chat_completion_with_retry(client, model="test-model")

    import asyncio
    asyncio.run(run())

    text = metrics.render()
    assert 'aichat_llm_calls_total{model="test-model",outcome="success",provider=""} 1' in text
    assert 'aichat_llm_call_duration_seconds_count{model="test-model",provider=""} 1' in text


def test_llm_failure_is_counted_with_error_type():
    class _Completions:
        def create(self, **kwargs):
            raise ValueError("bad request")

    client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))

    async def run():
        with pytest.raises(ValueError):
            await create_chat_completion_with_retry(client, model="test-model")

    import asyncio
    asyncio.run(run())

    text = metrics.render()
    assert 'aichat_llm_calls_total{model="test-model",outcome="ValueError",provider=""} 1' in text


# ---------------------------------------------------------------------------
# JSON 日志
# ---------------------------------------------------------------------------


def test_json_formatter_emits_single_json_line():
    record = logging.LogRecord(
        name="app.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    record.request_id = "rid-1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "app.test"
    assert payload["request_id"] == "rid-1"
    assert payload["message"] == "hello world"
    assert "exception" not in payload


def test_json_formatter_includes_exception_when_present():
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="app.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failed",
        args=(),
        exc_info=exc_info,
    )
    payload = json.loads(JsonFormatter().format(record))
    assert "RuntimeError: boom" in payload["exception"]


# ---------------------------------------------------------------------------
# 生命周期与停机映射
# ---------------------------------------------------------------------------


def test_lifecycle_flag_can_be_set_and_reset():
    assert lifecycle.is_shutting_down() is False
    lifecycle.mark_shutting_down()
    assert lifecycle.is_shutting_down() is True
    lifecycle.reset_shutting_down()
    assert lifecycle.is_shutting_down() is False


def test_shutdown_reason_maps_to_cancelled():
    assert messages._FINAL_STATUS_BY_STOP_REASON["shutdown"] == messages.CANCELLED
    assert messages._FINAL_STATUS_BY_STOP_REASON["stopped"] == messages.CANCELLED
    assert messages._FINAL_STATUS_BY_STOP_REASON["timed_out"] == messages.TIMED_OUT
