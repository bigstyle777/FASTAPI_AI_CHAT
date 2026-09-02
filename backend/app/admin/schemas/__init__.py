"""Admin（RBAC）域 Pydantic 模型。"""

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

__all__ = [
    "AdminUserResponse",
    "BootstrapAdminRequest",
    "PermissionResponse",
    "RoleCreateRequest",
    "RolePermissionsRequest",
    "RoleResponse",
    "RoleSummaryResponse",
    "UpdateUserRoleRequest",
]
