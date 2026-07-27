from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..core.database import get_db
from ..schemas import (
    CreateChatSessionRequest,
    ChatRequest,
    SessionListResponse,
    MessageListResponse,
    ChatResponse,
)
from ..services import create_session_service
from ..auth import get_current_user
from ..services import (
    send_message_service,
    get_sessions_service,
    get_messages_service,
    send_message_stream_service,
)
from ..ai import chat_with_ai_stream


router = APIRouter(prefix="/chat", tags=["Chat"])


@router.get("/sessions", response_model=SessionListResponse)
def get_sessions(user=Depends(get_current_user), db=Depends(get_db)):
    return get_sessions_service(db, user)


@router.post("/session")
def create_session(
    request: CreateChatSessionRequest,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    return create_session_service(db, user, request)


@router.get("/messages", response_model=MessageListResponse)
def get_messages(
    session_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    return get_messages_service(db, user, session_id)


@router.post("/message")
def send_message(
    request: ChatRequest, user=Depends(get_current_user), db=Depends(get_db)
):
    return send_message_service(db, user, request)


@router.post("/stream")
def chat_stream(
    request: ChatRequest, user=Depends(get_current_user), db=Depends(get_db)
):
    return StreamingResponse(
        send_message_stream_service(db, user, request),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
