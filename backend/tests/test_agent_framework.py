r"""agent 框架单元测试（不依赖真实 LLM / 数据库）。

用法（在 backend 目录下运行）：
    ..\.venv\Scripts\python.exe tests\test_agent_framework.py
"""

import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.agent.agent import run_agent, run_agent_stream
from app.agent.events import AgentPlanEvent, AgentStepEvent, AgentToolEvent
from app.agent.executor import execute_step
from app.agent.planner import _extract_json, create_plan
from app.agent.state import AgentState, PlanStep
from app.schemas import StreamDeltaEvent, StreamUsageEvent


# ---------------------------------------------------------------------------
# 假 OpenAI client
# ---------------------------------------------------------------------------


class FakeDelta:
    def __init__(self, content=None):
        self.content = content


class FakeChoice:
    def __init__(self, message=None, delta=None):
        self.message = message
        self.delta = delta


class FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class FakeFunction:
    def __init__(self, name="", arguments=""):
        self.name = name
        self.arguments = arguments


class FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = FakeFunction(name=name, arguments=arguments)


class FakeUsage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class FakeStreamChunk:
    def __init__(self, content=None, usage=None):
        self.usage = usage
        self.choices = (
            [FakeChoice(delta=FakeDelta(content=content))] if content else []
        )


class FakeResponse:
    def __init__(self, content=None, tool_calls=None):
        self.choices = [FakeChoice(message=FakeMessage(content, tool_calls))]


class PromptAwareCompletions:
    """按 system prompt 区分 planner / step / finalizer，返回配置好的内容。"""

    def __init__(self):
        self.plan_content = '{"steps": []}'
        self.step_content = "已完成"
        self.final_chunks = [FakeStreamChunk(content="你好"), FakeStreamChunk()]
        self.usage = FakeUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        self.raise_on_response_format = False

    def create(self, **kwargs):
        if self.raise_on_response_format and kwargs.get("response_format"):
            raise RuntimeError("provider 不支持 response_format")
        self.raise_on_response_format = False  # 只失败一次，验证 fallback

        if kwargs.get("stream"):
            return iter(self.final_chunks + [FakeStreamChunk(usage=self.usage)])

        messages = kwargs["messages"]
        for message in messages:
            if message.get("role") == "system" and "任务规划器" in message["content"]:
                return FakeResponse(self.plan_content)
            if message.get("role") == "system" and "任务执行器" in message["content"]:
                return FakeResponse(self.step_content)
            if message.get("role") == "system" and "任务总结器" in message["content"]:
                return FakeResponse("兜底总结")
        return FakeResponse("ok")


def make_client():
    return SimpleNamespace(
        chat=SimpleNamespace(completions=PromptAwareCompletions())
    )


# ---------------------------------------------------------------------------
# 内存版 tracer（行为对齐 AgentTracer，不依赖数据库）
# ---------------------------------------------------------------------------


class StubRecord:
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value

    def fail(self, error):
        self.value = {"error": error}


class StubTracer:
    def __init__(self, run_id=1):
        self.run_id = run_id
        self.points = []
        self.pending = []

    def point(self, stage, name, **kwargs):
        self.points.append({"stage": stage, "name": name, **kwargs})

    @contextmanager
    def span(self, stage, name, **kwargs):
        self.point(stage, name, status="started", **kwargs)
        record = StubRecord()
        try:
            yield record
        except Exception as error:
            self.point(
                stage,
                name,
                status="failed",
                error_message=str(error),
                **kwargs,
            )
            raise
        else:
            self.point(stage, name, status="completed", **kwargs)

    def emit(self, event):
        self.pending.append(event)

    def drain_events(self):
        events = self.pending
        self.pending = []
        return events


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


def test_extract_json():
    assert _extract_json('{"steps": []}') == {"steps": []}
    assert _extract_json('```json\n{"steps": []}\n```') == {"steps": []}
    assert _extract_json('前置文字 {"a": 1} 后置文字') == {"a": 1}


def test_create_plan_validates_steps():
    client = make_client()
    client.chat.completions.plan_content = json.dumps(
        {
            "steps": [
                {
                    "description": "算一下",
                    "tool": "calculator",
                    "args": {"a": 1, "b": 2, "operation": "add"},
                    "expected_output": "3",
                },
                {"description": "无工具步骤", "tool": None},
                {"description": "缺字段步骤"},
                {"description": "", "tool": "x"},
            ]
        },
        ensure_ascii=False,
    )
    steps = create_plan(
        client,
        "fake-model",
        [{"role": "user", "content": "任务"}],
        available_tools=["calculator"],
    )
    assert len(steps) == 3
    assert steps[0].tool == "calculator"
    assert steps[0].args == {"a": 1, "b": 2, "operation": "add"}
    assert steps[1].tool is None
    assert steps[2].args == {}


def test_create_plan_falls_back_without_response_format():
    client = make_client()
    client.chat.completions.raise_on_response_format = True
    client.chat.completions.plan_content = '{"steps": [{"description": "一步"}]}'
    steps = create_plan(client, "fake-model", [{"role": "user", "content": "任务"}])
    assert len(steps) == 1
    assert steps[0].description == "一步"


def test_execute_direct_tool_with_trace():
    tracer = StubTracer(run_id=7)
    step = PlanStep(
        description="计算",
        tool="calculator",
        args={"a": 2, "b": 3, "operation": "multiply"},
    )
    result = execute_step(
        None,
        "fake-model",
        step,
        index=0,
        tracer=tracer,
    )
    assert result.status == "completed"
    assert json.loads(result.output) == 6

    events = tracer.drain_events()
    assert [e.type for e in events] == ["agent_tool", "agent_tool"]
    assert events[0].status == "started"
    assert events[0].tool == "calculator"
    assert events[1].status == "completed"
    assert events[1].result == 6

    tool_points = [p for p in tracer.points if p["stage"] == "tool_call"]
    assert len(tool_points) == 2
    assert tool_points[0]["status"] == "started"
    assert tool_points[1]["status"] == "completed"
    assert tool_points[1]["duration_ms"] is not None


def test_execute_step_via_llm():
    client = make_client()
    tracer = StubTracer()
    step = PlanStep(description="解释什么是 RAG")
    result = execute_step(
        client,
        "fake-model",
        step,
        index=0,
        tracer=tracer,
    )
    assert result.status == "completed"
    assert result.output == "已完成"
    assert any(p["stage"] == "llm" and p["status"] == "completed" for p in tracer.points)


def test_run_agent_stream_event_sequence():
    client = make_client()
    client.chat.completions.plan_content = json.dumps(
        {
            "steps": [
                {
                    "description": "计算 2+3",
                    "tool": "calculator",
                    "args": {"a": 2, "b": 3, "operation": "add"},
                }
            ]
        },
        ensure_ascii=False,
    )
    tracer = StubTracer(run_id=9)

    events = list(
        run_agent_stream(
            client,
            "fake-model",
            "帮我算 2+3",
            messages=[{"role": "user", "content": "帮我算 2+3"}],
            tracer=tracer,
        )
    )

    types = [
        event.type if hasattr(event, "type") else "state"
        for event in events
    ]
    assert types == [
        "agent_plan",
        "agent_step",  # started
        "agent_tool",  # started
        "agent_tool",  # completed
        "agent_step",  # completed
        "delta",
        "usage",
        "state",
    ]

    plan_event = events[0]
    assert isinstance(plan_event, AgentPlanEvent)
    assert plan_event.run_id == 9
    assert plan_event.steps[0]["tool"] == "calculator"

    tool_events = [e for e in events if isinstance(e, AgentToolEvent)]
    assert tool_events[1].result == 5

    state = events[-1]
    assert isinstance(state, AgentState)
    assert state.status == "completed"
    assert state.final_answer == "你好"
    assert state.total_tokens == 15

    stages = {p["stage"] for p in tracer.points}
    assert {"plan", "step", "tool_call", "finalize"} <= stages


def test_run_agent_non_stream():
    client = make_client()
    client.chat.completions.plan_content = '{"steps": [{"description": "纯回答"}]}'
    state = run_agent(
        client,
        "fake-model",
        "你好",
        messages=[{"role": "user", "content": "你好"}],
        tracer=StubTracer(),
    )
    assert state.status == "completed"
    assert state.final_answer == "你好"
    assert len(state.plan) == 1


def test_run_agent_empty_plan_yields_error():
    client = make_client()
    client.chat.completions.plan_content = '{"steps": []}'
    events = list(
        run_agent_stream(
            client,
            "fake-model",
            "任务",
            messages=[{"role": "user", "content": "任务"}],
            tracer=StubTracer(),
        )
    )
    assert events[0].type == "error"


def main():
    tests = [
        test_extract_json,
        test_create_plan_validates_steps,
        test_create_plan_falls_back_without_response_format,
        test_execute_direct_tool_with_trace,
        test_execute_step_via_llm,
        test_run_agent_stream_event_sequence,
        test_run_agent_non_stream,
        test_run_agent_empty_plan_yields_error,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"\n{len(tests)} tests passed")


if __name__ == "__main__":
    main()
