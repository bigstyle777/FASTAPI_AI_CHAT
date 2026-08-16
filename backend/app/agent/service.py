"""Agent 的 HTTP 服务层：接收请求、串起主循环、落库并输出 SSE。"""

import logging
from typing import Generator

from sqlalchemy.orm import Session

from ..core.sse import sse_event
from ..crud import (
    create_message,
    get_session_by_user,
    update_session,
)
from ..exceptions import BusinessError
from ..schemas import StreamErrorEvent, StreamUsageEvent, TokenUsage
from ..services.cache import (
    check_rate_limit,
    clear_generation_status,
    is_stop_requested,
)
from ..services.llm import _get_client, _get_user_ai_settings
from ..services.message_context import (
    get_branch_parent_message_id,
    load_chat_context,
)
from ..services.task.memory_queue import enqueue_memory_extraction
from ..services.task.title_queue import enqueue_session_title_generation
from .agent import run_agent_stream
from .events import AgentDoneEvent, AgentPlanEvent
from .repo import create_agent_run, update_agent_run
from .state import AgentState
from .trace import AgentTracer

logger = logging.getLogger(__name__)


def agent_stream_service(db: Session, user: dict, request) -> Generator[str, None, None]:
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

        parent_id = get_branch_parent_message_id(db, session_id)
        user_message = create_message(
            db,
            session_id,
            "user",
            message,
            parent_id=parent_id,
        )
        update_session(db, session_id, message)
        enqueue_session_title_generation(session_id, message, user_id)

        history = load_chat_context(db, session_id, message)
        messages = history

        run = create_agent_run(
            db,
            session_id=session_id,
            user_id=user_id,
            user_input=message,
        )
        tracer = AgentTracer(db, run.id)

        api_key, provider = _get_user_ai_settings(user_id=user_id, db=db)
        result = _get_client(api_key=api_key, provider=provider)
        if not result:
            raise BusinessError("当前 AI 服务暂不可用，请先在个人中心配置 API Key")
        client, model = result

        context = {"db": db, "user_id": user_id}
        ai_reply = ""
        usage = TokenUsage()
        plan_payload = None
        final_state: AgentState | None = None
        failed = False

        for event in run_agent_stream(
            client,
            model,
            message,
            messages=messages,
            context=context,
            tracer=tracer,
            should_stop=lambda: is_stop_requested(session_id),
        ):
            if isinstance(event, AgentState):
                final_state = event
                continue
            if isinstance(event, StreamErrorEvent):
                failed = True
            if isinstance(event, AgentPlanEvent):
                plan_payload = event.steps
            elif isinstance(event, StreamUsageEvent):
                usage = event.usage
            elif hasattr(event, "type") and event.type == "delta":
                ai_reply += event.content
            yield sse_event(event.type, event)

        if failed:
            status = "failed"
        elif is_stop_requested(session_id):
            status = "stopped"
        else:
            status = "completed"
        update_agent_run(
            db,
            run.id,
            status=status,
            plan=plan_payload,
            final_answer=ai_reply,
            model=usage.model or model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            error_message=final_state.error if final_state else None,
        )

        if ai_reply.strip():
            create_message(
                db=db,
                session_id=session_id,
                role="assistant",
                content=ai_reply,
                model=usage.model or model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                parent_id=user_message.id,
            )
            update_session(db, session_id, ai_reply)
        enqueue_memory_extraction(user_id, message)

        yield sse_event("done", AgentDoneEvent(run_id=run.id, status=status))
        clear_generation_status(session_id)

    except Exception as error:  # noqa: BLE001
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
