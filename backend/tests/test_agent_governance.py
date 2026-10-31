"""阶段 3 Agent 工程化验收测试（不依赖真实 LLM）。

覆盖 3A / 3B / 3C 的验收项：
    - 幂等键重复请求不重复执行；每用户单运行
    - 整轮 deadline 超时 -> timed_out；步骤失败即停
    - 断连 / 停止 -> stopped
    - 启动清扫把遗留 running 落为 failed
    - 目标链降级：主目标失败切备用，首 chunk 后与 401 不切换
    - usage 全链路累加（规划 + 执行 + 总结）
    - planner 坏 JSON 重试；工具超时与输出截断

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_agent_governance.py -v
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, AuthenticationError

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.agent.service as service_mod  # noqa: E402
import app.core.llm_gateway as llm_gateway  # noqa: E402
import app.core.retry as retry_mod  # noqa: E402
from app.agent.agent import run_agent_stream  # noqa: E402
from app.agent.events import AgentPlanEvent  # noqa: E402
from app.agent.models import AgentRun  # noqa: E402
from app.agent.planner import create_plan  # noqa: E402
from app.agent.repo import create_agent_run, sweep_stale_agent_runs  # noqa: E402
from app.agent.state import AgentState  # noqa: E402
from app.agent.tool_calling import execute_tool_call  # noqa: E402
from app.chat.repository import create_session  # noqa: E402
from app.chat.schemas import StreamDeltaEvent, StreamUsageEvent, TokenUsage  # noqa: E402
from app.core.ai_client import ModelTarget  # noqa: E402
from app.tools import TOOL_REGISTRY  # noqa: E402
from app.usage.models import LlmUsageLog  # noqa: E402

BASE = "https://api.test/v1"


def _run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    return [event async for event in agen]


def _event_names(events):
    return [e.split("\n", 1)[0].removeprefix("event: ") for e in events]


def _request() -> httpx.Request:
    return httpx.Request("POST", f"{BASE}/chat/completions")


def _status_error(cls, status: int):
    return cls("boom", response=httpx.Response(status, request=_request()), body=None)


# ---------------------------------------------------------------------------
# 假 LLM 客户端
# ---------------------------------------------------------------------------


class _Usage:
    def __init__(self, prompt=0, completion=0):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.total_tokens = prompt + completion


class _Delta:
    def __init__(self, content=None):
        self.content = content
        self.tool_calls = None


class _Chunk:
    def __init__(self, content=None, usage=None):
        self.usage = usage
        self.choices = [SimpleNamespace(delta=_Delta(content))] if content else []


def _response(content, usage=None):
    message = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage if usage is not None else _Usage(),
    )


class _AgentCompletions:
    """按 system prompt 区分规划 / 执行 / 总结，返回可配置结果。"""

    def __init__(
        self,
        *,
        plan='{"steps": []}',
        step_reply="完成",
        step_error=None,
        final_contents=("你好",),
        usage=(10, 5),
    ):
        self.plan = plan
        self.step_reply = step_reply
        self.step_error = step_error
        self.final_contents = final_contents
        self.usage = usage
        self.step_calls = 0

    async def create(self, **kwargs):
        if kwargs.get("stream"):
            return self._astream()

        messages = kwargs["messages"]
        system = next(
            (m["content"] for m in messages if m.get("role") == "system"), ""
        )
        usage = _Usage(*self.usage)
        if "任务规划器" in system:
            return _response(self.plan, usage)
        if "任务执行器" in system:
            self.step_calls += 1
            if self.step_error is not None:
                raise self.step_error
            return _response(self.step_reply, usage)
        return _response("兜底", usage)

    async def _astream(self):
        for content in self.final_contents:
            yield _Chunk(content)
        yield _Chunk(usage=_Usage(*self.usage))


def _agent_client(**kwargs):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=_AgentCompletions(**kwargs))
    )


def _plan_json(*steps):
    return json.dumps({"steps": steps}, ensure_ascii=False)


def _last_state(events):
    states = [e for e in events if isinstance(e, AgentState)]
    assert states, "主循环没有产出最终状态"
    return states[-1]


# ---------------------------------------------------------------------------
# 3C · 规划与执行健壮性
# ---------------------------------------------------------------------------


class _BadThenGoodCompletions:
    def __init__(self):
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        content = (
            "这不是 JSON"
            if self.calls == 1
            else '{"steps": [{"description": "一步"}]}'
        )
        return _response(content)


def test_planner_retries_bad_json_then_succeeds():
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_BadThenGoodCompletions())
    )
    steps = _run(
        create_plan(
            client,
            "test-model",
            [{"role": "user", "content": "任务"}],
            available_tools=["calculator"],
        )
    )
    assert len(steps) == 1
    assert steps[0].description == "一步"


def test_step_failure_stops_remaining_steps():
    client = _agent_client(
        plan=_plan_json(
            {"description": "第一步"},
            {"description": "第二步"},
        ),
        step_error=RuntimeError("执行炸了"),
    )
    events = _run(
        _collect(
            run_agent_stream(
                client,
                "test-model",
                "任务",
                messages=[{"role": "user", "content": "任务"}],
            )
        )
    )
    state = _last_state(events)
    assert state.status == "failed"
    assert state.error == "执行炸了"
    # 失败即停：第二步没有被执行
    assert len(state.results) == 1
    assert client.chat.completions.step_calls == 1


def test_deadline_skips_steps_and_marks_timed_out():
    from time import perf_counter

    client = _agent_client(
        plan=_plan_json({"description": "第一步"}, {"description": "第二步"})
    )
    events = _run(
        _collect(
            run_agent_stream(
                client,
                "test-model",
                "任务",
                messages=[{"role": "user", "content": "任务"}],
                deadline=perf_counter() - 1,
            )
        )
    )
    state = _last_state(events)
    assert state.status == "timed_out"
    assert state.results == []
    assert client.chat.completions.step_calls == 0


def test_should_stop_marks_run_stopped():
    client = _agent_client(plan=_plan_json({"description": "一步"}))

    async def should_stop():
        return True

    events = _run(
        _collect(
            run_agent_stream(
                client,
                "test-model",
                "任务",
                messages=[{"role": "user", "content": "任务"}],
                should_stop=should_stop,
            )
        )
    )
    state = _last_state(events)
    assert state.status == "stopped"
    # 停止后不再执行步骤
    assert client.chat.completions.step_calls == 0


def test_usage_accumulates_across_planner_executor_finalizer():
    client = _agent_client(plan=_plan_json({"description": "一步"}))
    events = _run(
        _collect(
            run_agent_stream(
                client,
                "test-model",
                "任务",
                messages=[{"role": "user", "content": "任务"}],
            )
        )
    )
    state = _last_state(events)
    # 规划 15 + 执行 15 + 总结 15
    assert state.total_tokens == 45

    usage_events = [e for e in events if isinstance(e, StreamUsageEvent)]
    assert usage_events[-1].usage.total_tokens == 45


def test_tool_timeout_returns_error_json():
    async def slow_tool():
        await asyncio.sleep(1)

    TOOL_REGISTRY["slow_tool"] = slow_tool
    try:
        content = _run(
            execute_tool_call(
                {
                    "id": "call_1",
                    "function": {"name": "slow_tool", "arguments": "{}"},
                },
                timeout_seconds=0.05,
            )
        )
    finally:
        TOOL_REGISTRY.pop("slow_tool", None)

    data = json.loads(content)
    assert data["error_type"] == "ToolTimeout"
    assert "超时" in data["error"]


def test_tool_output_is_truncated():
    TOOL_REGISTRY["huge_tool"] = lambda: "x" * 5000
    try:
        content = _run(
            execute_tool_call(
                {
                    "id": "call_2",
                    "function": {"name": "huge_tool", "arguments": "{}"},
                },
                max_output_chars=100,
            )
        )
    finally:
        TOOL_REGISTRY.pop("huge_tool", None)

    data = json.loads(content)
    assert data["truncated"] is True
    assert data["limit"] == 100
    assert len(data["preview"]) == 100


# ---------------------------------------------------------------------------
# 3B · 目标链降级
# ---------------------------------------------------------------------------


def _target(provider, model):
    return ModelTarget(provider, BASE, model, "key", False)


def _gateway_client(create_impl):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_impl))
    )


def _patch_gateway(monkeypatch, behaviours):
    """behaviours: model -> async create(**kwargs)。"""
    monkeypatch.setattr(
        llm_gateway,
        "get_async_client_for",
        lambda target: _gateway_client(behaviours[target.model]),
    )
    # 关掉重试等待，让测试只验证"换目标"这一步
    monkeypatch.setattr(retry_mod.settings, "llm_retry_max_attempts", 1)


def test_complete_falls_back_to_backup_target(monkeypatch):
    called = []

    def main(**kwargs):
        called.append("main")
        raise APIConnectionError(request=_request())

    async def backup(**kwargs):
        called.append("backup")
        return _response("备用回答")

    _patch_gateway(monkeypatch, {"main": main, "backup": backup})

    result = _run(
        llm_gateway.complete(
            [_target("deepseek", "main"), _target("openai", "backup")],
            messages=[{"role": "user", "content": "hi"}],
        )
    )
    assert result.response.choices[0].message.content == "备用回答"
    assert result.target.model == "backup"
    assert called == ["main", "backup"]


def test_complete_does_not_switch_on_auth_error(monkeypatch):
    called = []

    def main(**kwargs):
        called.append("main")
        raise _status_error(AuthenticationError, 401)

    async def backup(**kwargs):
        called.append("backup")
        return _response("不该出现")

    _patch_gateway(monkeypatch, {"main": main, "backup": backup})

    with pytest.raises(AuthenticationError):
        _run(
            llm_gateway.complete(
                [_target("deepseek", "main"), _target("openai", "backup")],
                messages=[{"role": "user", "content": "hi"}],
            )
        )
    assert called == ["main"]


def test_stream_does_not_switch_after_first_chunk(monkeypatch):
    called = []

    async def main_stream():
        called.append("main")
        yield _Chunk("半")
        raise APIConnectionError(request=_request())

    async def main(**kwargs):
        return main_stream()

    async def backup(**kwargs):
        async def gen():
            called.append("backup")
            yield _Chunk("不该出现")

        return gen()

    _patch_gateway(monkeypatch, {"main": main, "backup": backup})

    chunks = []

    async def consume():
        async for chunk, target in llm_gateway.stream_completion(
            [_target("deepseek", "main"), _target("openai", "backup")],
            messages=[{"role": "user", "content": "hi"}],
        ):
            chunks.append((chunk, target))

    with pytest.raises(APIConnectionError):
        _run(consume())

    assert [c.choices[0].delta.content for c, _ in chunks] == ["半"]
    assert called == ["main"]


# ---------------------------------------------------------------------------
# 3A · 服务层：幂等 / 单运行 / 状态映射 / 清扫
# ---------------------------------------------------------------------------


@pytest.fixture
def session_id(db, user_id):
    return create_session(db, user_id, "Agent 治理测试会话").id


def _patch_service(monkeypatch, events_factory, calls=None):
    monkeypatch.setattr(service_mod, "budget_rejection_message", lambda *a, **k: None)
    monkeypatch.setattr(service_mod, "check_rate_limit", lambda *a, **k: True)
    monkeypatch.setattr(service_mod, "clear_generation_status", lambda *a, **k: None)
    monkeypatch.setattr(service_mod, "is_stop_requested", lambda *a: False)
    monkeypatch.setattr(
        service_mod, "enqueue_memory_extraction", lambda *a, **k: True
    )
    monkeypatch.setattr(
        service_mod,
        "get_user_ai_settings",
        lambda user_id=None, db=None: (None, "deepseek"),
    )
    monkeypatch.setattr(
        service_mod,
        "resolve_model_targets",
        lambda api_key=None, provider="deepseek": [
            ModelTarget("deepseek", BASE, "test-model", "key", False)
        ],
    )
    monkeypatch.setattr(
        service_mod, "get_async_client_for", lambda target: SimpleNamespace()
    )

    def fake_run_agent_stream(*args, **kwargs):
        if calls is not None:
            calls["n"] += 1

        async def gen():
            for event in events_factory():
                yield event

        return gen()

    monkeypatch.setattr(service_mod, "run_agent_stream", fake_run_agent_stream)


def _completed_events(answer="结果"):
    return lambda: [
        AgentPlanEvent(run_id=0, steps=[{"description": "一步"}]),
        StreamDeltaEvent(content=answer),
        StreamUsageEvent(
            usage=TokenUsage(
                provider="openai",
                model="backup-model",
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
            )
        ),
        AgentState(
            run_id=0,
            user_input="任务",
            status="completed",
            final_answer=answer,
            total_tokens=30,
        ),
    ]


def _agent_request(session_id, message="任务"):
    return SimpleNamespace(session_id=session_id, message=message)


def test_duplicate_idempotency_key_is_rejected_without_rerun(
    db, session_id, user_id, monkeypatch
):
    calls = {"n": 0}
    _patch_service(monkeypatch, _completed_events(), calls)
    user = {"user_id": user_id}

    first = _run(
        _collect(
            service_mod.agent_stream_service(
                db, user, _agent_request(session_id), idempotency_key="key-dup"
            )
        )
    )
    assert _event_names(first)[-1] == "done"
    assert calls["n"] == 1

    second = _run(
        _collect(
            service_mod.agent_stream_service(
                db, user, _agent_request(session_id), idempotency_key="key-dup"
            )
        )
    )
    assert _event_names(second) == ["error"]
    assert "请勿重复" in second[0]
    assert calls["n"] == 1
    assert db.query(AgentRun).filter_by(idempotency_key="key-dup").count() == 1


def test_running_run_rejects_new_request(db, session_id, user_id, monkeypatch):
    _patch_service(monkeypatch, _completed_events())
    create_agent_run(db, session_id=session_id, user_id=user_id, user_input="占用中")

    events = _run(
        _collect(
            service_mod.agent_stream_service(
                db, {"user_id": user_id}, _agent_request(session_id)
            )
        )
    )
    assert _event_names(events) == ["error"]
    assert "正在运行" in events[0]


def test_timed_out_run_is_persisted_with_full_usage(
    db, session_id, user_id, monkeypatch
):
    def timed_out_events():
        return [
            StreamUsageEvent(
                usage=TokenUsage(
                    provider="openai",
                    model="backup-model",
                    prompt_tokens=20,
                    completion_tokens=10,
                    total_tokens=30,
                )
            ),
            AgentState(
                run_id=0,
                user_input="任务",
                status="timed_out",
                total_tokens=30,
            ),
        ]

    _patch_service(monkeypatch, timed_out_events)

    events = _run(
        _collect(
            service_mod.agent_stream_service(
                db, {"user_id": user_id}, _agent_request(session_id)
            )
        )
    )
    assert _event_names(events)[-1] == "done"

    run = db.query(AgentRun).filter_by(user_id=user_id).order_by(AgentRun.id.desc()).first()
    assert run.status == "timed_out"
    assert run.total_tokens == 30
    assert run.model == "backup-model"

    ledger = db.query(LlmUsageLog).filter_by(user_id=user_id, scene="agent").one()
    assert ledger.provider == "openai"
    assert ledger.total_tokens == 30
    assert ledger.status == "failed"


def test_sweep_stale_runs_marks_failed(db, session_id, user_id):
    run = create_agent_run(
        db, session_id=session_id, user_id=user_id, user_input="遗留任务"
    )
    assert run is not None
    run.updated_at = datetime.utcnow() - timedelta(minutes=30)
    db.commit()

    assert sweep_stale_agent_runs(db, older_than_seconds=60) == 1
    db.refresh(run)
    assert run.status == "failed"
    assert "运行中断" in run.error_message
