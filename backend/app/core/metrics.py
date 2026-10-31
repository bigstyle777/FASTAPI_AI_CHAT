"""进程内轻量指标：只实现 Counter / Gauge / Histogram 与 Prometheus 文本渲染。

指标集合是封闭的、标签基数可控，用标准库实现足够，避免为一个
/metrics 端点引入运行时依赖。多副本部署时由采集侧按实例聚合。
"""

import threading

_LOCK = threading.Lock()
_METRICS: list["_Metric"] = []

DEFAULT_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def _label_key(labels: dict | None) -> tuple:
    return tuple(sorted((labels or {}).items()))


def _escape(value: object) -> str:
    return (
        str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    )


def _format_labels(labels: tuple) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{name}="{_escape(value)}"' for name, value in labels)
    return "{" + inner + "}"


def _format_value(value: float) -> str:
    return str(int(value)) if value == int(value) else repr(float(value))


class _Metric:
    kind = ""
    buckets: tuple = ()

    def __init__(self, name: str, help_text: str, label_names: tuple = ()):
        self.name = name
        self.help = help_text
        self.label_names = label_names
        self._values: dict[tuple, object] = {}
        with _LOCK:
            _METRICS.append(self)

    def _samples(self) -> list[str]:
        raise NotImplementedError


class Counter(_Metric):
    """单调递增计数。"""

    kind = "counter"

    def inc(self, labels: dict | None = None, value: float = 1.0) -> None:
        key = _label_key(labels)
        with _LOCK:
            self._values[key] = self._values.get(key, 0.0) + value

    def _samples(self) -> list[str]:
        return [
            f"{self.name}{_format_labels(key)} {_format_value(value)}"
            for key, value in sorted(self._values.items())
        ]


class Gauge(_Metric):
    """可增可减的瞬时值。"""

    kind = "gauge"

    def set(self, value: float, labels: dict | None = None) -> None:
        with _LOCK:
            self._values[_label_key(labels)] = float(value)

    def inc(self, labels: dict | None = None, value: float = 1.0) -> None:
        key = _label_key(labels)
        with _LOCK:
            self._values[key] = self._values.get(key, 0.0) + value

    def dec(self, labels: dict | None = None, value: float = 1.0) -> None:
        self.inc(labels, -value)

    def _samples(self) -> list[str]:
        return [
            f"{self.name}{_format_labels(key)} {_format_value(value)}"
            for key, value in sorted(self._values.items())
        ]


class Histogram(_Metric):
    """累计分桶 + 总和 + 计数；桶边界固定，避免标签爆炸。"""

    kind = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str,
        label_names: tuple = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ):
        super().__init__(name, help_text, label_names)
        self.buckets = tuple(buckets)

    def observe(self, value: float, labels: dict | None = None) -> None:
        key = _label_key(labels)
        with _LOCK:
            data = self._values.setdefault(
                key,
                {"buckets": {bound: 0 for bound in self.buckets}, "sum": 0.0, "count": 0},
            )
            for bound in self.buckets:
                if value <= bound:
                    data["buckets"][bound] += 1
            data["sum"] += value
            data["count"] += 1

    def _samples(self) -> list[str]:
        lines: list[str] = []
        for key, data in sorted(self._values.items()):
            for bound in self.buckets:
                bound_labels = key + (("le", _format_value(bound)),)
                lines.append(
                    f"{self.name}_bucket{_format_labels(bound_labels)} {data['buckets'][bound]}"
                )
            inf_labels = key + (("le", "+Inf"),)
            lines.append(
                f"{self.name}_bucket{_format_labels(inf_labels)} {data['count']}"
            )
            lines.append(f"{self.name}_sum{_format_labels(key)} {_format_value(data['sum'])}")
            lines.append(f"{self.name}_count{_format_labels(key)} {data['count']}")
        return lines


# ---- 本服务实际使用的指标 ----

HTTP_REQUESTS = Counter(
    "aichat_http_requests_total",
    "HTTP 请求总数",
    ("method", "path", "status"),
)
HTTP_DURATION = Histogram(
    "aichat_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ("method", "path"),
)
LLM_CALLS = Counter(
    "aichat_llm_calls_total",
    "LLM 调用总数（outcome=success 或错误类型）",
    ("provider", "model", "outcome"),
)
LLM_DURATION = Histogram(
    "aichat_llm_call_duration_seconds",
    "LLM 单次调用耗时（秒，含重试）",
    ("provider", "model"),
)
SSE_ACTIVE = Gauge(
    "aichat_sse_active_connections",
    "当前活跃的 SSE 生成连接数",
)
AGENT_RUNS = Counter(
    "aichat_agent_runs_total",
    "Agent 运行结果总数",
    ("status",),
)
CELERY_QUEUE = Gauge(
    "aichat_celery_queue_length",
    "Celery 默认队列积压长度",
    ("queue",),
)


def refresh_queue_length(queue: str = "celery") -> None:
    """抓取时刷新队列长度；Redis 不可用则保留上一次值。"""
    try:
        from .redis import get_redis

        CELERY_QUEUE.set(int(get_redis().llen(queue)), {"queue": queue})
    except Exception:  # noqa: BLE001
        return


def render() -> str:
    lines: list[str] = []
    with _LOCK:
        metrics = list(_METRICS)
        for metric in metrics:
            lines.append(f"# HELP {metric.name} {metric.help}")
            lines.append(f"# TYPE {metric.name} {metric.kind}")
            lines.extend(metric._samples())
    return "\n".join(lines) + "\n"


def reset() -> None:
    """仅测试使用：清空所有指标数据。"""
    with _LOCK:
        for metric in _METRICS:
            metric._values.clear()
