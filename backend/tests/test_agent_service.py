"""agent_stream_service 全链路测试（真实测试库 + 假 LLM client）。

覆盖：
    成功路径     规划→执行→总结 的事件序列 / run 状态落库 / 消息持久化 / trace 落库
    会话校验     不存在的会话直接报错
    空消息       报错
    规划失败     run 标记 failed 并输出 error 事件
    client 不可用（未配置 API Key）报错

用法（在 backend 目录下运行）：
    ..\\.venv\\Scripts\\python.exe -m pytest tests\\test_agent_service.py -v
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

import app.agent.service as agent_service  # noqa: E402
from app.crud import (  # noqa: E402
    create_role,
    create_session,
    create_user,
    get_messages_by_session,
    get_role_by_name,
)
from app.models import AgentRun, AgentTracePoint  # noqa: E402
from app.agent.service import agent_stream_service  # noqa: E402


# ---------------------------------------------------------------------------
# 假 LLM client：按 system prompt 区分 planner / step / finalizer
# ---------------------------------------------------------------------------


class _FakeDelta:
    def __init__(self, content=None):
        self.content = content


class _FakeChoice:
    def __init__(self, message=None, delta=None):
        self.message = message
        self.delta = delta


class _FakeMessage:
    def __init__(self, content=None):
        self.content = content


class _FakeUsage:
    def __init__(self, prompt_tokens=0, completion_tokens=0, total_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _StreamChunk:
    def __init__(self, content=None, usage=None):
        self.usage = usage
        self.choices = [SimpleNamespace(delta=_FakeDelta(content))] if content else []


PLAN_WITH_TOOL = json.dumps(
    {
        "steps": [
            {
                "description": "计算 2+3",
                "tool": "calculator",
                "args": {"a": 2, "b": 3, "operation": "add"},
                "expected_output": "5",
            }
        ]
    },
    ensure_ascii=False,
)


class _ScriptedCompletions:
    """plan 用 PLAN_WITH_TOOL；step 不该被走到（有工具直调）；finalize 流式回答。"""

    def __init__(self, plan_content=PLAN_WITH_TOOL, final_chunks=None, fail_plan=False):
        self.plan_content = plan_content
        self.fail_plan = fail_plan
        self.final_chunks = final_chunks or [
            _StreamChunk(content="计算结果是 "),
            _StreamChunk(content="5"),
            _StreamChunk(usage=_FakeUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30)),
        ]

    def create(self, **kwargs):
        if kwargs.get("stream"):
            return iter(self.final_chunks)

        for message in kwargs["messages"]:
            if message.get("role") == "system":
                if "任务规划器" in message["content"]:
                    if self.fail_plan:
                        raise RuntimeError("planner down")
                    return SimpleNamespace(
                        choices=[_FakeChoice(message=_FakeMessage(self.plan_content))]
                    )
                if "任务执行器" in message["content"]:
                    return SimpleNamespace(
                        choices=[_FakeChoice(message=_FakeMessage("步骤完成"))]
                    )
        return SimpleNamespace(choices=[_FakeChoice(message=_FakeMessage("ok"))])


def _make_client(**kwargs):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=_ScriptedCompletions(**kwargs))
    )


def _make_session(db, tag="agent"):
    role = get_role_by_name(db, "user")
    if role is None:
        role = create_role(db, "user")
    user = create_user(db, f"{tag}_user", "hashed-password", role.id)
    session = create_session(db, user.id, "agent 测试会话")
    return user, session


def _request(session_id, message="帮我算 2+3"):
    return SimpleNamespace(session_id=session_id, message=message)


def _parse_sse(event_str: str) -> tuple[str, dict]:
    lines = event_str.strip().split("\n")
    return lines[0].removeprefix("event: "), json.loads(lines[1].removeprefix("data: "))


def _patch_client(monkeypatch, client):
    """让 get_client 返回假 client（绕过 API Key 配置）。"""
    monkeypatch.setattr(
        agent_service,
        "get_client",
        lambda api_key=None, provider=None: (client, "fake-model"),
    )


# ---------------------------------------------------------------------------
# 成功路径
# ---------------------------------------------------------------------------


def test_agent_stream_success_persists_everything(db, monkeypatch):
    user, session = _make_session(db)
    _patch_client(monkeypatch, _make_client())

    events = [
        _parse_sse(e)
        for e in agent_stream_service(db, {"user_id": user.id}, _request(session.id))
    ]

    # 事件序列：plan -> step started -> tool started/completed -> step completed -> delta -> usage -> done
    types = [name for name, _ in events]
    assert types[0] == "agent_plan"
    assert types[-1] == "done"
    assert "agent_tool" in types
    assert "delta" in types and "usage" in types

    done_payload = events[-1][1]
    assert done_payload["status"] == "completed"
    assert done_payload["run_id"] == 1

    # run 落库：完成状态 + 计划 + token 用量
    run = db.execute(select(AgentRun)).scalar_one()
    assert run.status == "completed"
    assert run.final_answer == "计算结果是 5"
    assert run.total_tokens == 30
    assert run.model == "fake-model"
    assert run.plan and run.plan[0]["tool"] == "calculator"

    # 消息持久化：用户消息 + AI 回复
    messages = get_messages_by_session(db, session.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].content == "计算结果是 5"
    assert messages[1].parent_id == messages[0].id
    assert messages[1].model == "fake-model"
    assert messages[1].total_tokens == 30

    # trace 落库：plan / step / tool_call / finalize
    stages = set(
        db.execute(select(AgentTracePoint.stage).where(AgentTracePoint.run_id == run.id))
        .scalars()
        .all()
    )
    assert {"plan", "step", "tool_call", "finalize"} <= stages


def test_agent_stream_user_message_content(db, monkeypatch):
    user, session = _make_session(db, tag="msg")
    _patch_client(monkeypatch, _make_client())

    list(
        agent_stream_service(
            db, {"user_id": user.id}, _request(session.id, message="  帮我算 2+3  ")
        )
    )

    messages = get_messages_by_session(db, session.id)
    assert messages[0].content == "帮我算 2+3"


# ---------------------------------------------------------------------------
# 校验与失败路径
# ---------------------------------------------------------------------------


def test_agent_stream_rejects_missing_session(db):
    user, _ = _make_session(db, tag="miss")
    events = [
        _parse_sse(e)
        for e in agent_stream_service(db, {"user_id": user.id}, _request(999999))
    ]
    assert events[-1][0] == "error"
    assert "会话不存在" in events[-1][1]["message"]


def test_agent_stream_rejects_blank_message(db):
    user, session = _make_session(db, tag="blank")
    events = [
        _parse_sse(e)
        for e in agent_stream_service(
            db, {"user_id": user.id}, _request(session.id, message="   ")
        )
    ]
    assert events[-1][0] == "error"
    assert "不能为空" in events[-1][1]["message"]


def test_agent_stream_without_client_reports_error(db, monkeypatch):
    user, session = _make_session(db, tag="nokey")
    monkeypatch.setattr(agent_service, "get_client", lambda api_key=None, provider=None: None)

    events = [
        _parse_sse(e)
        for e in agent_stream_service(db, {"user_id": user.id}, _request(session.id))
    ]
    assert events[-1][0] == "error"
    assert "API Key" in events[-1][1]["message"]
    # 用户消息已落库，但没有 assistant 回复
    messages = get_messages_by_session(db, session.id)
    assert [m.role for m in messages] == ["user"]


def test_agent_stream_planner_failure_marks_run_failed(db, monkeypatch):
    user, session = _make_session(db, tag="fail")
    _patch_client(monkeypatch, _make_client(fail_plan=True))

    events = [
        _parse_sse(e)
        for e in agent_stream_service(db, {"user_id": user.id}, _request(session.id))
    ]

    # 规划失败：流里有 error 事件，末尾以 status=failed 的 done 终结
    types = [name for name, _ in events]
    assert "error" in types
    assert types[-1] == "done"
    assert "规划失败" in next(p for n, p in events if n == "error")["message"]
    assert events[-1][1]["status"] == "failed"

    run = db.execute(select(AgentRun)).scalar_one()
    assert run.status == "failed"
    assert run.error_message, "run 落库时应带上错误信息，方便事后查错"


def test_agent_stream_rate_limited(db, monkeypatch):
    user, session = _make_session(db, tag="rate")
    _patch_client(monkeypatch, _make_client())
    monkeypatch.setattr(agent_service, "check_rate_limit", lambda **kwargs: False)

    events = [
        _parse_sse(e)
        for e in agent_stream_service(db, {"user_id": user.id}, _request(session.id))
    ]
    assert events[-1][0] == "error"
    assert "频繁" in events[-1][1]["message"]
    # 没有创建 run
    assert db.execute(select(AgentRun)).scalars().all() == []
