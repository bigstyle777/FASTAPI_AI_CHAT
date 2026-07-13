from pydantic import BaseModel, Field
from typing import Optional


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=50)


class LoginRequest(BaseModel):
    username: str
    password: str


class CreateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100, description="聊天会话的标题")


class ChatRequest(BaseModel):
    session_id: int = Field(description="聊天会话的ID")
    message: str = Field(
        min_length=1, max_length=1000, description="用户发送的消息内容"
    )


class RegisterResponse(BaseModel):
    success: bool
    message: str


class LoginResponse(BaseModel):
    success: bool
    access_token: str
    token_type: str = "bearer"


class SessionResponse(BaseModel):
    session_id: int
    title: str
    last_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SessionListResponse(BaseModel):
    success: bool
    sessions: list[SessionResponse]


class MessageResponse(BaseModel):
    role: str
    content: str


class MessageListResponse(BaseModel):
    success: bool
    messages: list[MessageResponse]


class ChatResponse(BaseModel):
    success: bool
    reply: str


class SettingsRequest(BaseModel):
    api_key: Optional[str] = None
    provider: str = "deepseek"


class SettingsResponse(BaseModel):
    success: bool
    api_key: Optional[str] = None
    provider: str = "deepseek"
