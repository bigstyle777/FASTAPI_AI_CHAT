"""Agent 的 HTTP 服务层：接收请求、串起主循环、落库并输出 SSE。"""

import logging
from typing import Generator

from sqlalchemy.orm import Session

from ..core.sse import sse_event
from ..crud import get_session_by_user
from ..exceptions import BusinessError
from ..schemas import StreamErrorEvent, StreamUsageEvent, TokenUsage
from ..services.cache import (
    check_rate_limit,
    clear_generation_status,
    is_stop_requested,
)
from ..services.ai_client import get_client, get_user_ai_settings
from ..services.message_context import load_chat_context
from ..services.message_persistence import (
    persist_assistant_message,
    persist_user_message,
)
from ..services.task.memory_queue import enqueue_memory_extraction
from .agent import run_agent_stream
from .events import AgentDoneEvent, AgentPlanEvent
from .repo import create_agent_run, update_agent_run
from .state import AgentState
from .trace import AgentTracer

logger = logging.getLogger(__name__)


def _resolve_run_status(failed: bool, session_id: int) -> str:
    if failed:
        return "failed"
    if is_stop_requested(session_id):
        return "stopped"
    return "completed"


class _RunOutcome:
    """从 agent 流事件中收集需要落库的信息。

    ai_reply / usage / plan / error_message / final_state 这几项
    总是随事件流同时演进、结束时一起写进 agent_runs，收成一个对象
    避免 service 里散落五个局部变量。
    """

    def __init__(self):
        self.ai_reply = ""
        self.usage = TokenUsage()
        self.plan = None
        self.error_message = None
        self.final_state: AgentState | None = None

    @property
    def failed(self) -> bool:
        return self.error_message is not None

    def record(self, event) -> None:
        """消费一个事件，更新落库所需状态（不负责推送）。"""
        if isinstance(event, AgentState):
            self.final_state = event
        elif isinstance(event, StreamErrorEvent):
            self.error_message = event.message
        elif isinstance(event, AgentPlanEvent):
            self.plan = event.steps
        elif isinstance(event, StreamUsageEvent):
            self.usage = event.usage
        elif getattr(event, "type", None) == "delta":
            self.ai_reply += event.content

    def resolved_error_message(self) -> str | None:
        if self.error_message:
            return self.error_message
        if self.final_state is not None:
            return self.final_state.error
        return None


def agent_stream_service(
    db: Session, user: dict, request
) -> Generator[str, None, None]:
    """POST /agent/stream 的 SSE 生成器。"""
    user_id = user["user_id"]
    session_id = request.session_id

    allowed = check_rate_limit(
        key=f"rate_limit:agent:{user_id}",
        limit=200,
        expire_seconds=60 * 60,
    )
    if not allowed:
        yield sse_event("error", StreamErrorEvent(message="请求太频繁，请稍后重试"))
        return

    clear_generation_status(session_id)
    run = None
    tracer = None

    try:
        message = request.message.strip()
        if not message:
            raise BusinessError("消息不能为空")
        session = get_session_by_user(db, session_id, user_id)
        if not session:
            raise BusinessError("会话不存在或已删除")

        user_message = persist_user_message(db, user_id, session_id, message)

        messages = load_chat_context(db, session_id, message)

        run = create_agent_run(
            db,
            session_id=session_id,
            user_id=user_id,
            user_input=message,
        )
        tracer = AgentTracer(db, run.id)

        api_key, provider = get_user_ai_settings(user_id=user_id, db=db)
        result = get_client(api_key=api_key, provider=provider)
        if not result:
            raise BusinessError("当前 AI 服务暂不可用，请先在个人中心配置 API Key")
        client, model = result

        context = {"db": db, "user_id": user_id}
        outcome = _RunOutcome()

        for event in run_agent_stream(
            client,
            model,
            message,
            messages=messages,
            context=context,
            tracer=tracer,
            should_stop=lambda: is_stop_requested(session_id),
        ):
            outcome.record(event)
            if isinstance(event, AgentState):
                continue
            yield sse_event(event.type, event)

        status = _resolve_run_status(outcome.failed, session_id)
        update_agent_run(
            db,
            run.id,
            status=status,
            plan=outcome.plan,
            final_answer=outcome.ai_reply,
            model=outcome.usage.model or model,
            prompt_tokens=outcome.usage.prompt_tokens,
            completion_tokens=outcome.usage.completion_tokens,
            total_tokens=outcome.usage.total_tokens,
            error_message=outcome.resolved_error_message(),
        )

        if outcome.ai_reply.strip():
            persist_assistant_message(
                db,
                session_id,
                outcome.ai_reply,
                model=outcome.usage.model or model,
                prompt_tokens=outcome.usage.prompt_tokens,
                completion_tokens=outcome.usage.completion_tokens,
                total_tokens=outcome.usage.total_tokens,
                parent_id=user_message.id,
            )
        enqueue_memory_extraction(user_id, message)

        yield sse_event("done", AgentDoneEvent(run_id=run.id, status=status))
        clear_generation_status(session_id)

    except Exception as error:
        logger.exception("Agent 运行失败")
        if tracer is not None and run is not None:
            tracer.point(
                "error",
                "agent_failed",
                status="failed",
                error_message=str(error),
            )
            update_agent_run(db, run.id, status="failed", error_message=str(error))
        yield sse_event("error", StreamErrorEvent(message=f"Error: {str(error)}"))
