"""Pydantic 请求/响应模型。

按领域拆分到子模块，这里统一再导出，保持 ``from app.schemas import X`` 的既有调用方式不变。
"""

from .admin import (
    AdminUserResponse,
    BootstrapAdminRequest,
    PermissionResponse,
    RoleCreateRequest,
    RolePermissionsRequest,
    RoleResponse,
    RoleSummaryResponse,
    UpdateUserRoleRequest,
)
from .auth import (
    CaptchaResponse,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
    UserProfileResponse,
)
from .chat import (
    ActionResponse,
    ChatRequest,
    ChatSessionUpdateRequest,
    ChatSessionUpdateResponse,
    CreateChatSessionRequest,
    DeleteMessagesResponse,
    MessageListResponse,
    MessageResponse,
    MessageUpdateRequest,
    SessionListResponse,
    SessionResponse,
)
from .memory import (
    MemoryCreateRequest,
    MemoryListResponse,
    MemoryMutationResponse,
    MemoryResponse,
    MemoryUpdateRequest,
)
from .settings import SettingsRequest, SettingsResponse
from .stream import (
    StreamDeltaEvent,
    StreamDoneEvent,
    StreamErrorEvent,
    StreamEvent,
    StreamUsageEvent,
    TokenUsage,
)

__all__ = [
    # admin
    "AdminUserResponse",
    "BootstrapAdminRequest",
    "PermissionResponse",
    "RoleCreateRequest",
    "RolePermissionsRequest",
    "RoleResponse",
    "RoleSummaryResponse",
    "UpdateUserRoleRequest",
    # auth
    "CaptchaResponse",
    "LoginRequest",
    "LoginResponse",
    "LogoutResponse",
    "RegisterRequest",
    "RegisterResponse",
    "UserProfileResponse",
    # chat
    "ActionResponse",
    "ChatRequest",
    "ChatSessionUpdateRequest",
    "ChatSessionUpdateResponse",
    "CreateChatSessionRequest",
    "DeleteMessagesResponse",
    "MessageListResponse",
    "MessageResponse",
    "MessageUpdateRequest",
    "SessionListResponse",
    "SessionResponse",
    # memory
    "MemoryCreateRequest",
    "MemoryListResponse",
    "MemoryMutationResponse",
    "MemoryResponse",
    "MemoryUpdateRequest",
    # settings
    "SettingsRequest",
    "SettingsResponse",
    # stream
    "StreamDeltaEvent",
    "StreamDoneEvent",
    "StreamErrorEvent",
    "StreamEvent",
    "StreamUsageEvent",
    "TokenUsage",
]
