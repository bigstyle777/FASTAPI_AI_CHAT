from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas import (
    ActionResponse,
    ChatRequest,
    ChatSessionUpdate,
    ChatSessionUpdateResponse,
    CreateChatSessionRequest,
    DeleteMessagesResponse,
    MessageListResponse,
    MessageUpdateRequest,
    SessionListResponse,
)
from ..services.auth import get_current_user
from ..services.messages import (
    delete_message_service,
    get_messages_service,
    modify_message_services,
    send_message_service,
    send_message_stream_service,
)
from ..services.sessions import (
    create_session_service,
    delete_messages_service,
    delete_session_service,
    get_sessions_service,
    update_session_name_service,
)

router = APIRouter(prefix="/chat", tags=["Chat"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


# session增删改查
@router.get("/sessions", response_model=SessionListResponse)
def get_sessions(user: CurrentUser, db: Database):
    return get_sessions_service(db, user)


@router.post("/session")
def create_session(
    request: CreateChatSessionRequest,
    user: CurrentUser,
    db: Database,
):
    return create_session_service(db, user, request)


@router.delete("/session/{session_id}", response_model=ActionResponse)
def delete_session(
    session_id: int,
    user: CurrentUser,
    db: Database,
):
    return delete_session_service(db, user, session_id)


@router.post("/sessions/{session_id}", response_model=ChatSessionUpdateResponse)
def update_session_name(
    session_id: int, request: ChatSessionUpdate, user: CurrentUser, db: Database
):
    return update_session_name_service(db, user, session_id, request.title)


# messages
@router.get("/messages", response_model=MessageListResponse)
def get_messages(
    session_id: int,
    user: CurrentUser,
    db: Database,
):
    return get_messages_service(db, user, session_id)


@router.delete("/messages", response_model=DeleteMessagesResponse)
def delete_messages(
    session_id: int,
    user: CurrentUser,
    db: Database,
):
    return delete_messages_service(db, user, session_id)


# message增删改查
@router.post("/message")
def send_message(request: ChatRequest, user: CurrentUser, db: Database):
    return send_message_service(db, user, request)


@router.delete("/messages/{message_id}", response_model=ActionResponse)
def delete_message_api(message_id: int, user: CurrentUser, db: Database):
    return delete_message_service(db, user, message_id)


@router.put("/messages/{message_id}/stream")
def modify_message_api(
    message_id: int, request: MessageUpdateRequest, user: CurrentUser, db: Database
):
    return StreamingResponse(
        modify_message_services(db, user, message_id, request.content),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stream")
def chat_stream(
    request: ChatRequest,
    user: CurrentUser,
    db: Database,
):
    return StreamingResponse(
        send_message_stream_service(db, user, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
