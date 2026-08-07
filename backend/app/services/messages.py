from ..crud import (
    create_message,
    delete_message,
    delete_message_pair,
    delete_messages_after,
    get_message_by_id,
    get_messages_by_session,
    get_session_by_user,
    update_message,
    update_session,
)
from ..exceptions import BusinessError
from ..schemas import (
    StreamDeltaEvent,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamUsageEvent,
    TokenUsage,
)
from .cache import (
    check_rate_limit,
    clear_generation_status,
    invalidate_chat_cache,
    is_stop_requested,
    set_generation_status,
)
from .llm import chat_with_ai, chat_with_ai_stream
from .message_context import (
    get_branch_parent_message_id,
    load_chat_context,
    load_visible_messages,
    save_chat_context,
)
from .task.title_queue import enqueue_session_title_generation
from .utils import sse_event


def _validate_message_request(db, user, request):
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


def send_message_service(db, user, request):
    message = _validate_message_request(db, user, request)

    parent_id = get_branch_parent_message_id(db, request.session_id)
    user_message = create_message(
        db, request.session_id, "user", message, parent_id=parent_id
    )
    update_session(db, request.session_id, message)
    enqueue_session_title_generation(request.session_id, message, user["user_id"])

    history = load_visible_messages(db, request.session_id)
    messages = [{"role": item.role, "content": item.content} for item in history]
    ai_reply = chat_with_ai(messages=messages, user_id=user["user_id"], db=db)

    create_message(db, request.session_id, "assistant", ai_reply, parent_id=user_message.id)
    update_session(db, request.session_id, ai_reply)

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
    set_generation_status(session_id, "stop_requested")


def stream_ai_reply(db, user_id, session_id, messages, parent_id=None):
    clear_generation_status(session_id)
    ai_reply = ""
    usage = TokenUsage()

    for event in chat_with_ai_stream(messages, user_id=user_id, db=db):
        if is_stop_requested(session_id):
            break
        elif isinstance(event, StreamUsageEvent):
            usage = event.usage
        elif isinstance(event, StreamErrorEvent):
            clear_generation_status(session_id)
            yield sse_event(event.type, event)
            return
        elif isinstance(event, StreamDeltaEvent):
            ai_reply += event.content
            yield sse_event(event.type, event)

    create_message(
        db=db,
        session_id=session_id,
        role="assistant",
        content=ai_reply,
        model=usage.model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        parent_id=parent_id,
    )
    update_session(db, session_id, ai_reply)
    messages.append(
        {
            "role": "assistant",
            "content": ai_reply,
        }
    )
    save_chat_context(session_id, messages)
    yield sse_event("usage", StreamUsageEvent(usage=usage))
    yield sse_event("done", StreamDoneEvent())
    clear_generation_status(session_id)


def modify_message_services(db, user, message_id, new_content):
    new_content = new_content.strip()
    if not new_content:
        raise BusinessError("消息不能为空")

    message = get_message_by_id(db, message_id, user["user_id"])
    if not message:
        raise BusinessError("消息不存在")

    if message.role != "user":
        raise BusinessError("只能修改用户消息")

    message = update_message(db, message_id, new_content)
    assert message is not None
    delete_messages_after(db, message.session_id, message.id)
    invalidate_chat_cache(message.session_id)

    history = get_messages_by_session(db, message.session_id)
    messages = [{"role": item.role, "content": item.content} for item in history]

    yield from stream_ai_reply(
        db, user["user_id"], message.session_id, messages, parent_id=message.id
    )
    return {"success": True, "message": "修改成功"}


def send_message_stream_service(db, user, request):
    user_id = user["user_id"]
    allowed = check_rate_limit(
        key=f"rate_limit:chat:{user_id}", limit=500, expire_seconds=60 * 60
    )
    if not allowed:
        yield sse_event(
            "error",
            StreamErrorEvent(message="请求太频繁，请稍后重试"),
        )
        return

    try:
        try:
            message = _validate_message_request(db, user, request)
        except BusinessError as error:
            yield sse_event("error", StreamErrorEvent(message=str(error.message)))
            return

        parent_id = get_branch_parent_message_id(db, request.session_id)
        user_message = create_message(
            db, request.session_id, "user", message, parent_id=parent_id
        )
        update_session(db, request.session_id, message)
        enqueue_session_title_generation(request.session_id, message, user_id)

        messages = load_chat_context(db, request.session_id, message)

        yield from stream_ai_reply(
            db, user_id, request.session_id, messages, parent_id=user_message.id
        )

    except Exception as error:  # noqa: BLE001
        yield sse_event("error", StreamErrorEvent(message=f"Error: {str(error)}"))
