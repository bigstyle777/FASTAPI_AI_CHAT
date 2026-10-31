"""Agent 的 HTTP 服务层：接收请求、串起主循环、落库并输出 SSE。"""

import asyncio
import logging
from time import perf_counter
from typing import AsyncGenerator

from sqlalchemy.orm import Session

from ..chat.repository import get_session_by_user
from ..chat.schemas import StreamErrorEvent, StreamUsageEvent, TokenUsage
from ..chat.services.message_context import (
    build_memory_messages,
    load_chat_context,
)
from ..chat.services.message_persistence import (
    persist_assistant_message,
    persist_user_message,
)
from ..core.ai_client import get_async_client_for, get_user_ai_settings, resolve_model_targets
from ..core.blocking import run_blocking
from ..core.cache import (
    check_rate_limit,
    clear_generation_status,
    is_stop_requested,
)
from ..core.config import settings
from ..core.sse import sse_event
from ..core.exceptions import BusinessError
from ..core.lifecycle import is_shutting_down
from ..core.metrics import AGENT_RUNS, SSE_ACTIVE
from ..core.stop_signal import (
    StopWatcher,
    clear_stopped,
    is_stopped_locally,
    mark_stopped,
)
from ..task.memory_queue import enqueue_memory_extraction
from ..usage.service import budget_rejection_message, safe_record
from .agent import run_agent_stream
from .events import AgentDoneEvent, AgentPlanEvent
from .repo import (
    create_agent_run,
    get_agent_run_by_idempotency,
    get_running_agent_run,
    update_agent_run,
)
from .state import AgentState
from .trace import AgentTracer

logger = logging.getLogger(__name__)

# 断连检测降频到每秒一次，避免每个 token 都付一次 receive 轮询
_DISCONNECT_CHECK_INTERVAL_SECONDS = 1.0

# 进程内同时运行的 Agent 数闸门。打满即拒绝、不排队：排队只是把上游
# 超时和内存压力往后推，前端看到的仍然是"卡住"。
_agent_slots = asyncio.Semaphore(settings.agent_max_concurrent_runs)

_AGENT_BUSY_MESSAGE = "当前已有 Agent 任务正在运行，请等待完成或先停止"
_DUPLICATE_MESSAGE = "该请求已提交，请勿重复"


def _resolve_run_status(outcome: "_RunOutcome", session_id: int) -> str:
    """收尾时判断这一轮算哪种终态。

    主循环会产出带 status 的 AgentState；没走到那一步（规划就失败）
    时按错误标记/停止标记兜底。
    """
    state = outcome.final_state
    if state is not None and state.status in ("completed", "failed", "stopped", "timed_out"):
        return state.status
    if outcome.error_message:
        return "failed"
    if is_stopped_locally(session_id):
        return "stopped"
    return "completed"


_LEDGER_STATUS = {
    "completed": "success",
    "stopped": "cancelled",
    "timed_out": "failed",
    "failed": "failed",
}


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


async def agent_stream_service(
    db: Session,
    user: dict,
    request,
    *,
    is_disconnected=None,
    idempotency_key: str | None = None,
) -> AsyncGenerator[str, None]:
    """POST /agent/stream 的 SSE 生成器。

    全局并发闸门放在这里而不是路由层：被拒时仍以 SSE 错误事件返回，
    前端能正常收尾。
    """
    if _agent_slots.locked():
        yield sse_event(
            "error",
            StreamErrorEvent(message="当前 Agent 请求过多，请稍后再试"),
        )
        return

    await _agent_slots.acquire()
    SSE_ACTIVE.inc()
    try:
        async for event in _agent_stream_inner(
            db,
            user,
            request,
            is_disconnected=is_disconnected,
            idempotency_key=idempotency_key,
        ):
            yield event
    finally:
        SSE_ACTIVE.dec()
        _agent_slots.release()


async def _agent_stream_inner(
    db: Session,
    user: dict,
    request,
    *,
    is_disconnected=None,
    idempotency_key: str | None = None,
) -> AsyncGenerator[str, None]:
    user_id = user["user_id"]
    session_id = request.session_id
    started_at = perf_counter()

    allowed = await run_blocking(
        check_rate_limit,
        key=f"rate_limit:agent:{user_id}",
        limit=200,
        expire_seconds=60 * 60,
    )
    if not allowed:
        yield sse_event("error", StreamErrorEvent(message="请求太频繁，请稍后重试"))
        return

    rejection = await run_blocking(budget_rejection_message, user_id)
    if rejection:
        yield sse_event("error", StreamErrorEvent(message=rejection))
        return

    try:
        message = request.message.strip()
        if not message:
            raise BusinessError("消息不能为空")
        session = await run_blocking(get_session_by_user, db, session_id, user_id)
        if not session:
            raise BusinessError("会话不存在或已删除")
    except BusinessError as error:
        yield sse_event("error", StreamErrorEvent(message=str(error.message)))
        return

    await run_blocking(clear_generation_status, session_id)
    # 清掉上一轮可能残留的标记，避免它一直堵着这个会话
    clear_stopped(session_id)
    stop_watcher = StopWatcher(
        session_id, lambda: run_blocking(is_stop_requested, session_id)
    )
    run = None
    tracer = None

    try:
        # 幂等判重：双击/断网重试/刷新重发只跑一次、只扣一次费。
        # 唯一索引兜底并发竞态（create_agent_run 返回 None）。
        if idempotency_key:
            existing = await run_blocking(
                get_agent_run_by_idempotency, db, idempotency_key
            )
            if existing is not None:
                yield sse_event("error", StreamErrorEvent(message=_DUPLICATE_MESSAGE))
                return

        # 每用户同时只允许一个 Agent 运行
        if await run_blocking(get_running_agent_run, db, user_id) is not None:
            yield sse_event("error", StreamErrorEvent(message=_AGENT_BUSY_MESSAGE))
            return

        run = await run_blocking(
            create_agent_run,
            db,
            session_id=session_id,
            user_id=user_id,
            user_input=message,
            idempotency_key=idempotency_key,
        )
        if run is None:
            yield sse_event("error", StreamErrorEvent(message=_DUPLICATE_MESSAGE))
            return
        tracer = AgentTracer(db, run.id)

        user_message = await run_blocking(
            persist_user_message, db, user_id, session_id, message
        )

        history_messages = await run_blocking(load_chat_context, db, session_id, message)
        # 检索相关记忆，注入主循环。Agent 不写 chat 缓存，不会污染下一轮。
        memory_messages = await run_blocking(
            build_memory_messages,
            db,
            user_id,
            message,
            session_id,
        )
        messages = memory_messages + history_messages

        api_key, provider = await run_blocking(get_user_ai_settings, user_id=user_id, db=db)
        targets = resolve_model_targets(api_key=api_key, provider=provider)
        # targets 为空（没配 Key）或 openai 包缺失都会让 client 拿不到，
        # 两者对用户的表现一致，合并成一个出口避免重复判空
        client = get_async_client_for(targets[0]) if targets else None
        if client is None:
            raise BusinessError("当前 AI 服务暂不可用，请先在个人中心配置 API Key")
        model = targets[0].model

        # 断连与"用户点停止"合并成一个 should_stop：断连降频检查后
        # 直接标记停止，主循环下一次检查就会退出并正常收尾落库。
        next_disconnect_check = [0.0]

        async def should_stop() -> bool:
            if is_shutting_down():
                logger.info("服务停机，会话 %s 主动收尾", session_id)
                return True
            if await stop_watcher.should_stop():
                return True
            if is_disconnected is not None:
                now = perf_counter()
                if now >= next_disconnect_check[0]:
                    next_disconnect_check[0] = now + _DISCONNECT_CHECK_INTERVAL_SECONDS
                    if await is_disconnected():
                        logger.info("会话 %s 客户端断连，停止 Agent", session_id)
                        mark_stopped(session_id)
                        return True
            return False

        context = {"db": db, "user_id": user_id}
        outcome = _RunOutcome()
        deadline = perf_counter() + settings.agent_run_timeout_seconds

        async for event in run_agent_stream(
            client,
            model,
            message,
            messages=messages,
            context=context,
            tracer=tracer,
            should_stop=should_stop,
            targets=targets,
            provider=provider,
            deadline=deadline,
        ):
            outcome.record(event)
            if isinstance(event, AgentState):
                continue
            yield sse_event(event.type, event)

        status = _resolve_run_status(outcome, session_id)
        AGENT_RUNS.inc({"status": status})
        used_model = outcome.usage.model or model
        used_provider = outcome.usage.provider or provider
        await run_blocking(
            update_agent_run,
            db,
            run.id,
            status=status,
            plan=outcome.plan,
            final_answer=outcome.ai_reply,
            model=used_model,
            prompt_tokens=outcome.usage.prompt_tokens,
            completion_tokens=outcome.usage.completion_tokens,
            total_tokens=outcome.usage.total_tokens,
            error_message=outcome.resolved_error_message(),
        )
        await run_blocking(
            safe_record,
            db,
            user_id=user_id,
            session_id=session_id,
            scene="agent",
            provider=used_provider or "",
            model=used_model,
            usage=outcome.usage,
            status=_LEDGER_STATUS.get(status, "failed"),
            latency_ms=int((perf_counter() - started_at) * 1000),
            error=outcome.resolved_error_message(),
        )

        if outcome.ai_reply.strip():
            await run_blocking(
                persist_assistant_message,
                db,
                session_id,
                outcome.ai_reply,
                model=used_model,
                prompt_tokens=outcome.usage.prompt_tokens,
                completion_tokens=outcome.usage.completion_tokens,
                total_tokens=outcome.usage.total_tokens,
                parent_id=user_message.id,
            )
        await run_blocking(
            enqueue_memory_extraction, user_id, session_id, messages
        )

        yield sse_event("done", AgentDoneEvent(run_id=run.id, status=status))
        await run_blocking(clear_generation_status, session_id)
        clear_stopped(session_id)

    except Exception as error:
        logger.exception("Agent 运行失败")
        AGENT_RUNS.inc({"status": "failed"})
        clear_stopped(session_id)
        await run_blocking(clear_generation_status, session_id)
        if tracer is not None and run is not None:
            await tracer.point(
                "error",
                "agent_failed",
                status="failed",
                error_message=str(error),
            )
            await run_blocking(
                update_agent_run, db, run.id, status="failed", error_message=str(error)
            )
        yield sse_event("error", StreamErrorEvent(message=f"Error: {str(error)}"))
