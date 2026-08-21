from typing import Optional

from pydantic import BaseModel, Field


class CreateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100, description="聊天会话的标题")


class ChatRequest(BaseModel):
    session_id: int = Field(description="聊天会话的 ID")
    message: str = Field(
        min_length=1,
        max_length=1000,
        description="用户发送的消息内容",
    )


class MessageUpdateRequest(BaseModel):
    content: str = Field(min_length=1, max_length=1000)


class ChatSessionUpdateRequest(BaseModel):
    title: Optional[str] = Field(
        default=None, min_length=1, max_length=100, description="聊天会话的标题"
    )
    is_pinned: bool | None = None


class ChatSessionUpdateResponse(BaseModel):
    success: bool
    session_id: int
    title: str
    is_pinned: bool = False


class SessionResponse(BaseModel):
    session_id: int
    title: str
    last_message: Optional[str] = None
    is_pinned: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SessionListResponse(BaseModel):
    success: bool
    sessions: list[SessionResponse]


class MessageResponse(BaseModel):
    message_id: int
    role: str
    content: str
    is_inherited: bool = False
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class MessageListResponse(BaseModel):
    success: bool
    messages: list[MessageResponse]


class DeleteMessagesResponse(BaseModel):
    success: bool
    deleted_count: int = 0
    session_deleted: bool = False
    message: Optional[str] = None


class ActionResponse(BaseModel):
    success: bool
    message: Optional[str] = None
