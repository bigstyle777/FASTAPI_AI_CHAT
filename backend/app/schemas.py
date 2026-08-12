from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=6, max_length=50)


class LoginRequest(BaseModel):
    username: str
    password: str
    captcha_id: str = Field(min_length=1)
    captcha_code: str = Field(min_length=1, max_length=10)


class CaptchaResponse(BaseModel):
    success: bool
    captcha_id: str
    image: str
    expires_in: int


class CreateChatSessionRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100, description="聊天会话的标题")


class UpdateChatSessionRequest(BaseModel):
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


class RegisterResponse(BaseModel):
    success: bool
    message: str


class LoginResponse(BaseModel):
    success: bool
    access_token: str = ""
    token_type: str = "bearer"
    expires_in: int = 0
    message: Optional[str] = None


class LogoutResponse(BaseModel):
    success: bool
    message: str


class UserProfileResponse(BaseModel):
    success: bool
    user_id: int
    username: str
    role: str = "user"
    permissions: list[str] = Field(default_factory=list)


class PermissionResponse(BaseModel):
    permission_id: int
    code: str
    name: str
    description: Optional[str] = None


class RoleResponse(BaseModel):
    role_id: int
    name: str
    description: Optional[str] = None
    permissions: list[PermissionResponse] = Field(default_factory=list)


class RoleSummaryResponse(BaseModel):
    role_id: int
    name: str
    description: Optional[str] = None
    permissions: list[str] = Field(default_factory=list)


class AdminUserResponse(BaseModel):
    user_id: int
    username: str
    role: str
    permissions: list[str] = Field(default_factory=list)


class UpdateUserRoleRequest(BaseModel):
    role_id: int


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=50)
    description: Optional[str] = Field(default=None, max_length=255)


class RolePermissionsRequest(BaseModel):
    permission_codes: list[str] = Field(default_factory=list)


class BootstrapAdminRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)


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


class ChatResponse(BaseModel):
    success: bool
    reply: str


class ActionResponse(BaseModel):
    success: bool
    message: Optional[str] = None


class SettingsRequest(BaseModel):
    api_key: Optional[str] = None
    provider: str = "deepseek"
    embedding_api_key: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_model: Optional[str] = None


class SettingsResponse(BaseModel):
    success: bool
    api_key: Optional[str] = None
    provider: str = "deepseek"
    embedding_api_key: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_model: Optional[str] = None


class ChatSessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=100, description="聊天会话的标题")


class ChatSessionUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=100, description="聊天会话的标题")
    is_pinned: bool | None = None


class ChatSessionUpdateResponse(BaseModel):
    success: bool
    session_id: int
    title: str
    is_pinned: bool = False


# token字段
class TokenUsage(BaseModel):
    model: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class StreamDeltaEvent(BaseModel):
    type: Literal["delta"] = "delta"
    content: str


class StreamUsageEvent(BaseModel):
    type: Literal["usage"] = "usage"
    usage: TokenUsage


class StreamErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


class StreamDoneEvent(BaseModel):
    type: Literal["done"] = "done"


StreamEvent = Union[
    StreamDeltaEvent,
    StreamUsageEvent,
    StreamErrorEvent,
    StreamDoneEvent,
]
