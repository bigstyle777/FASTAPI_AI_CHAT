from typing import Optional

from pydantic import BaseModel, Field


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
