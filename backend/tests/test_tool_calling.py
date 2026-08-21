"""tool_calling 主循环测试（不依赖真实 LLM）。

覆盖：
    execute_tool_call  单次工具执行（成功 / 工具不存在 / 参数非法 / 执行异常）
    _call_tool         db / user_id 上下文注入
    run_tool_loop      非流式工具循环（无工具直返 / 工具后回答）
    stream_with_tools  流式分片 tool_calls 聚合 + 最终回答 + usage

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_tool_calling.py -v
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.services.tool_calling as tool_calling  # noqa: E402
from app.schemas import StreamDeltaEvent, StreamUsageEvent  # noqa: E402
from app.services.tool_calling import (  # noqa: E402
    _assembled_tool_calls,
    _call_tool,
    _consume_stream_round,
    _merge_tool_call_fragment,
    execute_tool_call,
    run_tool_loop,
    stream_with_tools,
)


def _tool_call(name, arguments, call_id="call_1"):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


# ---------------------------------------------------------------------------
# execute_tool_call
# ---------------------------------------------------------------------------


def test_execute_tool_call_success():
    result = execute_tool_call(_tool_call("calculator", '{"a": 2, "b": 3, "operation": "add"}'))
    assert json.loads(result) == 5


def test_execute_tool_call_unknown_tool():
    result = json.loads(execute_tool_call(_tool_call("nope", "{}")))
    assert result["error_type"] == "ToolNotFound"
    assert "calculator" in result["available_tools"]


def test_execute_tool_call_invalid_json_arguments():
    result = json.loads(execute_tool_call(_tool_call("calculator", "{broken")))
    assert result["error_type"] == "InvalidArguments"


def test_execute_tool_call_tool_exception_becomes_error_json():
    result = json.loads(
        execute_tool_call(_tool_call("calculator", '{"a": 1, "b": 0, "operation": "divide"}'))
    )
    assert result["error_type"] == "ValueError"
    assert result["tool"] == "calculator"


def test_execute_tool_call_reports_via_callbacks():
    calls = []
    results = []
    execute_tool_call(
        _tool_call("calculator", '{"a": 2, "b": 2, "operation": "multiply"}'),
        on_tool_call=lambda cid, name, args: calls.append((cid, name, args)),
        on_tool_result=lambda cid, name, res, **kw: results.append((cid, name, res, kw)),
    )
    assert calls == [("call_1", "calculator", {"a": 2, "b": 2, "operation": "multiply"})]
    assert results[0][2] == 4
    assert results[0][3]["duration_ms"] is not None


# ---------------------------------------------------------------------------
# _call_tool 上下文注入
# ---------------------------------------------------------------------------


def test_call_tool_injects_db_and_user_id():
    def probe(x, db=None, user_id=None):
        return {"x": x, "db": db, "user_id": user_id}

    result = _call_tool(probe, {"x": 1}, {"db": "DB", "user_id": 7, "ignored": True})
    assert result == {"x": 1, "db": "DB", "user_id": 7}


def test_call_tool_without_context_skips_injection():
    def probe(x, db=None):
        return db

    assert _call_tool(probe, {"x": 1}, None) is None


# ---------------------------------------------------------------------------
# run_tool_loop（非流式）
# ---------------------------------------------------------------------------


class _ScriptedCompletions:
    """按调用顺序返回预置的非流式响应。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _response(message):
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _fragment_call(call_id, name, arguments):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_run_tool_loop_without_tools_returns_untouched(monkeypatch):
    monkeypatch.setattr(tool_calling, "ALL_TOOLS", [])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=_ScriptedCompletions([]))
    )
    messages = [{"role": "user", "content": "hi"}]
    history, content = run_tool_loop(client, "m", messages)
    assert content is None
    assert history == messages
    assert client.chat.completions.calls == []


def test_run_tool_loop_executes_tool_then_final_answer(monkeypatch):
    monkeypatch.setattr(tool_calling, "ALL_TOOLS", [{"type": "function", "function": {"name": "calculator"}}])
    completions = _ScriptedCompletions(
        [
            _response(_message(tool_calls=[_fragment_call("call_1", "calculator", '{"a": 2, "b": 3, "operation": "add"}')])),
            _response(_message(content="答案是 5")),
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    history, content = run_tool_loop(client, "m", [{"role": "user", "content": "算 2+3"}])

    assert content == "答案是 5"
    tool_messages = [m for m in history if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert json.loads(tool_messages[0]["content"]) == 5
    assert tool_messages[0]["tool_call_id"] == "call_1"


# ---------------------------------------------------------------------------
# stream_with_tools（流式）
# ---------------------------------------------------------------------------


def _stream_chunk(content=None, tool_calls=None, usage=None):
    delta = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        usage=usage,
        choices=[] if usage else [SimpleNamespace(delta=delta)],
    )


def test_stream_with_tools_aggregates_fragments_and_answers(monkeypatch):
    monkeypatch.setattr(tool_calling, "ALL_TOOLS", [{"type": "function", "function": {"name": "calculator"}}])

    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)

    round1 = [
        _stream_chunk(tool_calls=[SimpleNamespace(index=0, id="call_1", function=SimpleNamespace(name="calculator", arguments='{"a": 2'))]),
        _stream_chunk(tool_calls=[SimpleNamespace(index=0, id=None, function=SimpleNamespace(name=None, arguments=', "b": 3, "operation": "add"}'))]),
    ]
    round2 = [
        _stream_chunk(content="结果是"),
        _stream_chunk(content="5"),
        _stream_chunk(usage=usage),
    ]

    completions = _ScriptedCompletions([iter(round1), iter(round2)])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    events = list(
        stream_with_tools(client, "test-model", [{"role": "user", "content": "算 2+3"}])
    )

    assert [e.type for e in events] == ["delta", "delta", "usage"]
    assert [e.content for e in events[:2]] == ["结果是", "5"]
    assert isinstance(events[-1], StreamUsageEvent)
    assert events[-1].usage.total_tokens == 15
    assert events[-1].usage.model == "test-model"

    # 第二轮请求应带着工具结果
    second_call_messages = completions.calls[1]["messages"]
    tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
    assert json.loads(tool_messages[0]["content"]) == 5


def test_stream_with_tools_plain_answer_without_tool_calls(monkeypatch):
    monkeypatch.setattr(tool_calling, "ALL_TOOLS", [{"type": "function", "function": {"name": "calculator"}}])
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    chunks = iter(
        [
            _stream_chunk(content="你好"),
            _stream_chunk(usage=usage),
        ]
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=_ScriptedCompletions([chunks])))

    events = list(
        stream_with_tools(client, "test-model", [{"role": "user", "content": "hi"}])
    )

    assert [e.type for e in events] == ["delta", "usage"]
    assert events[0].content == "你好"


def test_stream_with_tools_without_tools_single_round(monkeypatch):
    monkeypatch.setattr(tool_calling, "ALL_TOOLS", [])
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    chunks = iter([_stream_chunk(content="ok"), _stream_chunk(usage=usage)])
    completions = _ScriptedCompletions([chunks])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    events = list(
        stream_with_tools(client, "test-model", [{"role": "user", "content": "hi"}])
    )

    assert [e.type for e in events] == ["delta", "usage"]
    # 无工具时请求不应携带 tools 参数
    assert "tools" not in completions.calls[0]


# ---------------------------------------------------------------------------
# 重构后抽出的聚合单元（可直接单测）
# ---------------------------------------------------------------------------


def _fragment(index, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def test_merge_tool_call_fragment_accumulates_arguments():
    parts = {}
    _merge_tool_call_fragment(parts, [_fragment(0, call_id="call_1", name="calculator", arguments='{"a": 2')])
    _merge_tool_call_fragment(parts, [_fragment(0, arguments=', "b": 3}')])
    assert parts == {
        0: {"id": "call_1", "name": "calculator", "arguments": '{"a": 2, "b": 3}'}
    }


def test_merge_tool_call_fragment_keeps_parallel_calls_by_index():
    parts = {}
    _merge_tool_call_fragment(
        parts,
        [
            _fragment(0, call_id="call_a", name="calculator", arguments="{}"),
            _fragment(1, call_id="call_b", name="weather", arguments="{}"),
        ],
    )
    assert sorted(parts) == [0, 1]
    assert parts[1]["name"] == "weather"


def test_assembled_tool_calls_sorted_by_index():
    parts = {
        1: {"id": "call_b", "name": "weather", "arguments": '{"city": "北京"}'},
        0: {"id": "call_a", "name": "calculator", "arguments": "{}"},
    }
    calls = _assembled_tool_calls(parts)
    assert [c["id"] for c in calls] == ["call_a", "call_b"]
    assert calls[0]["type"] == "function"


def test_consume_stream_round_returns_aggregated_result():
    usage = SimpleNamespace(prompt_tokens=3, completion_tokens=4, total_tokens=7)
    chunks = iter(
        [
            _stream_chunk(content="你好"),
            _stream_chunk(
                tool_calls=[SimpleNamespace(index=0, id="call_1", function=SimpleNamespace(name="calculator", arguments="{}"))]
            ),
            _stream_chunk(usage=usage),
        ]
    )

    events = []
    generator = _consume_stream_round(chunks)
    while True:
        try:
            events.append(next(generator))
        except StopIteration as stop:
            round_result = stop.value
            break

    assert [e.type for e in events] == ["delta"]
    assert events[0].content == "你好"
    assert round_result.content == "你好"
    assert round_result.usage is usage
    assert _assembled_tool_calls(round_result.tool_call_parts)[0]["id"] == "call_1"
