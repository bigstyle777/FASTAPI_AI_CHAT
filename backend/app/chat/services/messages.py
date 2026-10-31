import asyncio
import logging
from time import perf_counter

from ...core.blocking import run_blocking
from ...core.cache import (
    check_rate_limit,
    clear_generation_status,
    invalidate_chat_cache,
    is_stop_requested,
    set_generation_status,
)
from ...core.config import settings
from ...core.lifecycle import is_shutting_down
from ...core.metrics import SSE_ACTIVE
from ...core.sse import sse_event
from ...core.exceptions import BusinessError
from ...core.stop_signal import StopWatcher, clear_stopped, mark_stopped
from ...task.memory_queue import enqueue_memory_extraction
from ...memory.extractor import build_extraction_input
from ...usage.service import budget_rejection_message, safe_record
from .message_context import build_memory_messages
from ..repository import (
    delete_message,
    delete_message_pair,
    delete_messages_after,
    get_assistant_message_by_parent,
    get_message_by_id,
    get_messages_by_session,
    get_session_by_user,
    update_message,
    update_session,
)
from ..schemas import (
    StreamDeltaEvent,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamUsageEvent,
    TokenUsage,
)
from .llm import chat_with_ai, chat_with_ai_stream
from .generation import (
    CANCELLED,
    COMPLETED,
    FAILED,
    PENDING,
    RUNNING,
    TIMED_OUT,
    create_generation,
    delete_generation,
    find_by_idempotency_key,
    get_running_generation,
    transition,
)
from .message_context import (
    load_chat_context,
    load_model_context,
    load_visible_messages,
    save_chat_context,
    to_chat_messages,
    trim_chat_messages,
)
from .message_persistence import persist_assistant_message, persist_user_message

logger = logging.getLogger(__name__)


def _get_validated_message(db, user, request):
    message = request.message.strip()
    if not message:
        raise BusinessError("消息不能为空")

    session = get_session_by_user(db, request.session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    return message


def delete_message_service(db, user, message_id):
    message = get_message_by_id(db, message_id, user["user_id"])

    if not message:
        raise BusinessError("消息不存在")

    if message.role == "user":
        delete_message_pair(db, message.id, message.session_id)
    else:
        delete_message(db, message.id, user["user_id"])

    remaining_messages = get_messages_by_session(db, message.session_id)
    last_message = remaining_messages[-1].content if remaining_messages else None
    update_session(db, message.session_id, last_message)
    invalidate_chat_cache(message.session_id)

    return {"success": True, "message": "删除成功"}


async def send_message_service(db, user, request):
    message = await run_blocking(_get_validated_message, db, user, request)
    rejection = await run_blocking(budget_rejection_message, user["user_id"])
    if rejection:
        raise BusinessError(rejection)
    user_message = await run_blocking(
        persist_user_message, db, user["user_id"], request.session_id, message
    )

    # 非流式接口同样走"喂模型"的上下文，别把整个会话历史塞进去
    history_messages = await run_blocking(load_model_context, db, request.session_id)
    memory_messages = await run_blocking(
        build_memory_messages,
        db,
        user["user_id"],
        message,
        request.session_id,
    )
    messages = memory_messages + history_messages
    ai_reply = await chat_with_ai(
        messages=messages, user_id=user["user_id"], db=db
    )

    await run_blocking(
        persist_assistant_message,
        db,
        request.session_id,
        ai_reply,
        parent_id=user_message.id,
    )
    # 只把"最近几条上下文 + 本轮 user/assistant"交给提取，不把整段历史
    # 和注入的记忆 system 消息喂回去：episodic 一律 CREATE 不幂等，
    # 旧历史每轮重提就会堆出大量近似重复的记忆。
    conversation = build_extraction_input(
        history_messages, user_text=message, assistant_text=ai_reply
    )
    await run_blocking(
        enqueue_memory_extraction,
        user["user_id"],
        request.session_id,
        conversation,
    )

    return {"success": True}


def get_messages_service(db, user, session_id):
    session = get_session_by_user(db, session_id, user["user_id"])
    if not session:
        raise BusinessError("会话不存在或已删除")

    history = load_visible_messages(db, session_id)
    messages = [
        {
            "message_id": item.id,
            "role": item.role,
            "content": item.content,
            "is_inherited": item.session_id != session_id,
            "model": item.model,
            "prompt_tokens": item.prompt_tokens,
            "completion_tokens": item.completion_tokens,
            "total_tokens": item.total_tokens,
        }
        for item in history
    ]
    return {"success": True, "messages": messages}


def stop_generation_service(session_id, user):
    # 进程内先记一笔，流式循环下一次检查就能立刻看到，不用等 Redis 兜底
    mark_stopped(session_id)
    set_generation_status(session_id, "stop_requested")


# 断连检测降频到每秒一次，避免每个 token 都付一次 receive 轮询
_DISCONNECT_CHECK_INTERVAL_SECONDS = 1.0

# 退出原因 -> 生成任务终态。None 表示正常跑完
_FINAL_STATUS_BY_STOP_REASON = {
    None: COMPLETED,
    "stopped": CANCELLED,
    "disconnected": CANCELLED,
    "shutdown": CANCELLED,
    "timed_out": TIMED_OUT,
}


# 进程内同时运行的生成数闸门。打满即拒绝、不排队：排队只是把上游超时
# 和内存压力往后推，前端看到的仍然是"卡住"。
_generation_slots = asyncio.Semaphore(settings.chat_max_concurrent_generations)

_USER_BUSY_MESSAGE = "当前已有回答正在生成，请等待完成或先停止"


async def stream_ai_reply(
    db,
    user_id,
    session_id,
    messages,
    parent_id=None,
    history_messages=None,
    *,
    user_message_id=None,
    idempotency_key=None,
    is_disconnected=None,
):
    """并发闸门 + 真正的生成流程。

    闸门放在这里而不是路由层：发消息、改消息重生成两条路径都要经过，
    且被拒时仍以 SSE 错误事件返回，前端能正常收尾。
    """
    if _generation_slots.locked():
        yield sse_event(
            "error",
            StreamErrorEvent(message="当前回答请求过多，请稍后再试"),
        )
        return

    await _generation_slots.acquire()
    SSE_ACTIVE.inc()
    try:
        async for event in _stream_generation(
            db,
            user_id,
            session_id,
            messages,
            parent_id=parent_id,
            history_messages=history_messages,
            user_message_id=user_message_id,
            idempotency_key=idempotency_key,
            is_disconnected=is_disconnected,
        ):
            yield event
    finally:
        SSE_ACTIVE.dec()
        _generation_slots.release()


async def _stream_generation(
    db,
    user_id,
    session_id,
    messages,
    parent_id=None,
    history_messages=None,
    *,
    user_message_id=None,
    idempotency_key=None,
    is_disconnected=None,
):
    await run_blocking(clear_generation_status, session_id)
    # 清掉上一轮可能残留的标记，避免它一直堵着这个会话
    clear_stopped(session_id)
    started_at = perf_counter()
    # 生成任务生命周期的 DB 写（建任务/流转/落库）全部用同步调用、不走
    # 线程池：每次生成只有几次、约 1ms，换来的是断连取消后收尾逻辑仍能
    # 执行——取消域内任何 await 都会立刻再抛 CancelledError，同步调用不受影响。
    generation = create_generation(
        db,
        session_id=session_id,
        user_id=user_id,
        user_message_id=user_message_id,
        idempotency_key=idempotency_key,
    )
    if generation is None:
        # 幂等键撞唯一索引：并发重复提交漏过上游判重的兜底
        yield sse_event("error", StreamErrorEvent(message="重复提交，已忽略"))
        return
    transition(db, generation.id, from_status=(PENDING,), to_status=RUNNING)

    stop_watcher = StopWatcher(
        session_id, lambda: run_blocking(is_stop_requested, session_id)
    )
    ai_reply = ""
    usage = TokenUsage()
    # 整条流的总预算：上游挂死时由 request 超时兜底（60s），这里防的是
    # "流没断但永远生成不完"的慢速消耗。
    deadline = perf_counter() + settings.llm_stream_max_seconds
    next_disconnect_check = 0.0
    stop_reason = None  # None | stopped | disconnected | timed_out

    stream = chat_with_ai_stream(messages, user_id=user_id, db=db)
    try:
        async for event in stream:
            now = perf_counter()
            if now > deadline:
                logger.warning(
                    "会话 %s 流式生成超过 %ss 总预算，主动截断",
                    session_id,
                    settings.llm_stream_max_seconds,
                )
                stop_reason = "timed_out"
                break
            if is_shutting_down():
                logger.info("服务停机，会话 %s 主动收尾", session_id)
                stop_reason = "shutdown"
                break
            if is_disconnected is not None and now >= next_disconnect_check:
                next_disconnect_check = now + _DISCONNECT_CHECK_INTERVAL_SECONDS
                if await is_disconnected():
                    logger.info("会话 %s 客户端断连，停止生成", session_id)
                    stop_reason = "disconnected"
                    break
            if await stop_watcher.should_stop():
                stop_reason = "stopped"
                break
            elif isinstance(event, StreamUsageEvent):
                usage = event.usage
            elif isinstance(event, StreamErrorEvent):
                transition(
                    db, generation.id,
                    from_status=(RUNNING,), to_status=FAILED, error=event.message,
                )
                safe_record(
                    db,
                    user_id=user_id,
                    session_id=session_id,
                    scene="chat",
                    provider=usage.provider or "",
                    model=usage.model,
                    usage=usage,
                    status="failed",
                    latency_ms=int((perf_counter() - started_at) * 1000),
                    error=event.message,
                )
                clear_generation_status(session_id)
                clear_stopped(session_id)
                yield sse_event(event.type, event)
                return
            elif isinstance(event, StreamDeltaEvent):
                ai_reply += event.content
                yield sse_event(event.type, event)
    except asyncio.CancelledError:
        # 客户端断连且 Starlette 先一步取消了响应任务
        stop_reason = "disconnected"
    except Exception as error:  # noqa: BLE001
        transition(
            db, generation.id,
            from_status=(RUNNING,), to_status=FAILED,
            error=f"{type(error).__name__}: {error}"[:500],
        )
        clear_generation_status(session_id)
        clear_stopped(session_id)
        raise

    # 主动中断（停止/超时）时确定性地关闭上游流，不等 GC 回收连接；
    # 断连路径的连接由 httpx 的取消处理负责释放
    if stop_reason in ("stopped", "timed_out", "shutdown"):
        await stream.aclose()

    # 统一收场（全同步）：部分内容落库 + 终态流转 + 清信号
    persist_assistant_message(
        db,
        session_id,
        ai_reply,
        model=usage.model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        parent_id=parent_id,
    )
    transition(
        db, generation.id,
        from_status=(RUNNING,),
        to_status=_FINAL_STATUS_BY_STOP_REASON[stop_reason],
        model=usage.model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
    )
    safe_record(
        db,
        user_id=user_id,
        session_id=session_id,
        scene="chat",
        provider=usage.provider or "",
        model=usage.model,
        usage=usage,
        status="success" if stop_reason is None else "cancelled",
        latency_ms=int((perf_counter() - started_at) * 1000),
    )
    clear_generation_status(session_id)
    clear_stopped(session_id)

    if stop_reason == "disconnected":
        # 客户端已经走了：跳过异步收尾（取消域内 await 会立刻再抛），
        # 记忆提取对一个没送达到的回答也没有意义
        return

    # 用副本追加 assistant：history_messages 为 None 时 saved_messages 就是
    # messages 本体，就地 append 会把本轮 assistant 混进下面交给提取的
    # messages，造成"流式主路径看不到 assistant、改消息路径却看得到"的
    # 不一致。拷贝之后两条路径行为统一。
    saved_messages = list(history_messages if history_messages is not None else messages)
    saved_messages.append({"role": "assistant", "content": ai_reply})
    await run_blocking(save_chat_context, session_id, saved_messages)

    user_message_text = _last_user_message_text(messages)
    if user_message_text:
        # 本轮 user + assistant 才是提取对象，前面几条上下文只用于消解
        # 指代（"就用它吧"指的是什么）；build_extraction_input 会剔除
        # 注入的记忆 system 消息，避免把已有记忆再提取一遍。
        conversation = build_extraction_input(
            messages,
            user_text=user_message_text,
            assistant_text=ai_reply,
        )
        await run_blocking(
            enqueue_memory_extraction,
            user_id,
            session_id,
            conversation,
        )
    yield sse_event("usage", StreamUsageEvent(usage=usage))
    yield sse_event("done", StreamDoneEvent())


def _last_user_message_text(messages: list[dict[str, str]]) -> str:
    """从对话历史里找最后一条用户消息，作为记忆提取的输入。"""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = (message.get("content") or "").strip()
            if content:
                return content
    return ""


async def modify_message_service(db, user, message_id, new_content, is_disconnected=None):
    if get_running_generation(db, user["user_id"]) is not None:
        yield sse_event("error", StreamErrorEvent(message=_USER_BUSY_MESSAGE))
        return

    new_content = new_content.strip()
    if not new_content:
        raise BusinessError("消息不能为空")

    message = await run_blocking(get_message_by_id, db, message_id, user["user_id"])
    if not message:
        raise BusinessError("消息不存在")

    if message.role != "user":
        raise BusinessError("只能修改用户消息")

    rejection = await run_blocking(budget_rejection_message, user["user_id"])
    if rejection:
        yield sse_event("error", StreamErrorEvent(message=rejection))
        return

    message = await run_blocking(update_message, db, message_id, new_content)
    assert message is not None
    await run_blocking(delete_messages_after, db, message.session_id, message.id)
    await run_blocking(invalidate_chat_cache, message.session_id)

    history = await run_blocking(get_messages_by_session, db, message.session_id)
    messages = trim_chat_messages(to_chat_messages(history))

    async for event in stream_ai_reply(
        db,
        user["user_id"],
        message.session_id,
        messages,
        parent_id=message.id,
        history_messages=messages,
        user_message_id=message.id,
        is_disconnected=is_disconnected,
    ):
        yield event


async def send_message_stream_service(
    db, user, request, *, idempotency_key=None, is_disconnected=None
):
    user_id = user["user_id"]
    allowed = await run_blocking(
        check_rate_limit,
        key=f"rate_limit:chat:{user_id}",
        limit=settings.chat_rate_limit_per_hour,
        expire_seconds=60 * 60,
    )
    if not allowed:
        yield sse_event(
            "error",
            StreamErrorEvent(message="请求太频繁，请稍后重试"),
        )
        return

    rejection = await run_blocking(budget_rejection_message, user_id)
    if rejection:
        yield sse_event("error", StreamErrorEvent(message=rejection))
        return

    try:
        try:
            message = await run_blocking(_get_validated_message, db, user, request)
        except BusinessError as error:
            yield sse_event("error", StreamErrorEvent(message=str(error.message)))
            return

        # 幂等判重：同一 Idempotency-Key 的用户意图（双击/断网重试/刷新重发）
        # 只生成一次、只扣一次费。唯一索引兜底并发竞态。
        if idempotency_key:
            existing = find_by_idempotency_key(db, idempotency_key)
            if existing is not None:
                if existing.status in (PENDING, RUNNING):
                    yield sse_event(
                        "error",
                        StreamErrorEvent(message="同一条消息正在生成中，请勿重复提交"),
                    )
                    return
                if existing.status == COMPLETED and existing.user_message_id is not None:
                    replayed = get_assistant_message_by_parent(
                        db, existing.session_id, existing.user_message_id
                    )
                    if replayed is not None:
                        # 重放已落库的回答，不重新调用模型
                        yield sse_event("delta", StreamDeltaEvent(content=replayed.content))
                        yield sse_event(
                            "usage",
                            StreamUsageEvent(
                                usage=TokenUsage(
                                    model=replayed.model,
                                    prompt_tokens=replayed.prompt_tokens,
                                    completion_tokens=replayed.completion_tokens,
                                    total_tokens=replayed.total_tokens,
                                )
                            ),
                        )
                        yield sse_event("done", StreamDoneEvent())
                        return
                # FAILED/CANCELLED/TIMED_OUT：允许同键重试，先清掉旧终态记录
                delete_generation(db, existing.id)

        # 每用户同时只允许一条生成：异步生成期间刷新/双击都不会叠出第二条。
        # 放在幂等判重之后：同键重复有更精确的提示
        if get_running_generation(db, user_id) is not None:
            yield sse_event("error", StreamErrorEvent(message=_USER_BUSY_MESSAGE))
            return

        user_message = await run_blocking(
            persist_user_message, db, user_id, request.session_id, message
        )

        # 缓存路径（history_messages）与模型路径（messages）必须分开：
        # 记忆检索结果会变化、也会过时，烤进缓存会"凝固"并污染后续会话。
        history_messages = await run_blocking(
            load_chat_context, db, request.session_id, message
        )
        memory_messages = await run_blocking(
            build_memory_messages,
            db,
            user_id,
            message,
            request.session_id,
        )
        messages = memory_messages + history_messages

        async for event in stream_ai_reply(
            db,
            user_id,
            request.session_id,
            messages,
            parent_id=user_message.id,
            history_messages=history_messages,
            user_message_id=user_message.id,
            idempotency_key=idempotency_key,
            is_disconnected=is_disconnected,
        ):
            yield event

    except Exception as error:  # noqa: BLE001
        yield sse_event("error", StreamErrorEvent(message=f"Error: {str(error)}"))
