from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..crud import (
    create_role,
    get_permissions,
    get_role_by_id,
    get_role_by_name,
    get_users_with_roles,
)
from ..schemas import (
    AdminUserResponse,
    PermissionResponse,
    RoleCreateRequest,
    RoleResponse,
    RolePermissionsRequest,
    UpdateUserRoleRequest,
)
from ..services.auth import get_current_user
from ..services.rbac import (
    assign_role_to_user,
    build_user_context,
    list_roles_with_permissions,
    get_role_permissions,
    set_role_permissions,
    sync_default_rbac,
)

router = APIRouter(prefix="/admin", tags=["Admin"])
CurrentUser = Annotated[dict[str, Any], Depends(get_current_user)]
Database = Annotated[Session, Depends(get_db)]


def _ensure_admin(user: dict[str, Any]):
    if user.get("role") != "admin":
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ADMIN ACCESS REQUIRED",
        )


@router.get("/bootstrap")
def bootstrap_admin(user: CurrentUser, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)
    return {
        "success": True,
        "message": "RBAC initialized",
    }


@router.get("/dashboard")
def dashboard(user: CurrentUser, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)

    users = get_users_with_roles(db)
    permissions = get_permissions(db)

    return {
        "success": True,
        "summary": {
            "users": len(users),
            "roles": len(list_roles_with_permissions(db)),
            "permissions": len(permissions),
            "admin_users": sum(1 for item in users if getattr(item.role, "name", "") == "admin"),
        },
    }


@router.get("/users", response_model=list[AdminUserResponse])
def list_users(user: CurrentUser, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)
    return [build_user_context(item) for item in get_users_with_roles(db)]


@router.patch("/users/{user_id}/role")
def update_user_role(user_id: int, request: UpdateUserRoleRequest, user: CurrentUser, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)

    target_user = next((item for item in get_users_with_roles(db) if item.id == user_id), None)
    if not target_user:
        return {"success": False, "message": "user not found"}

    role = get_role_by_id(db, request.role_id)
    if not role:
        return {"success": False, "message": "role not found"}

    assign_role_to_user(db, target_user, role)
    return {"success": True, "message": "role updated"}


@router.get("/roles", response_model=list[RoleResponse])
def list_roles(user: CurrentUser, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)
    return list_roles_with_permissions(db)


@router.post("/roles", response_model=RoleResponse)
def create_role_api(request: RoleCreateRequest, user: CurrentUser, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)

    existing = get_role_by_name(db, request.name.strip())
    if existing:
        return {
            "role_id": existing.id,
            "name": existing.name,
            "description": existing.description,
            "permissions": get_role_permissions(db, existing.id),
        }

    role = create_role(db, request.name.strip(), description=request.description)
    return {
        "role_id": role.id,
        "name": role.name,
        "description": role.description,
        "permissions": [],
    }


@router.put("/roles/{role_id}/permissions")
def update_role_permissions(
    role_id: int,
    request: RolePermissionsRequest,
    user: CurrentUser,
    db: Database,
):
    _ensure_admin(user)
    sync_default_rbac(db)
    set_role_permissions(db, role_id, request.permission_codes)
    return {"success": True, "message": "permissions updated"}


@router.get("/permissions", response_model=list[PermissionResponse])
def list_permissions(user: CurrentUser, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)
    permissions = get_permissions(db)
    return [
        {
            "permission_id": permission.id,
            "code": permission.code,
            "name": permission.name,
            "description": permission.description,
        }
        for permission in permissions
    ]


@router.get("/roles/{role_id}")
def get_role_detail(user: CurrentUser, role_id: int, db: Database):
    _ensure_admin(user)
    sync_default_rbac(db)

    role = get_role_by_id(db, role_id)
    if not role:
        return {"success": False, "message": "role not found"}

    return {
        "success": True,
        "role_id": role.id,
        "name": role.name,
        "description": role.description,
        "permissions": get_role_permissions(db, role.id),
    }
