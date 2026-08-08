from __future__ import annotations

from typing import Any, Iterable

from fastapi import Depends, HTTPException, status

from ..core.security import hash_password
from ..crud import (
    create_permission,
    create_role,
    create_user,
    get_permission_by_code,
    get_permission_by_id,
    get_permissions,
    get_role_by_id,
    get_role_by_name,
    get_roles,
    get_user_by_username,
    get_users_with_roles,
    replace_role_permissions,
)

DEFAULT_ROLE_USER = "user"
DEFAULT_ROLE_ADMIN = "admin"

DEFAULT_PERMISSIONS: list[dict[str, str]] = [
    {"code": "chat:read", "name": "Chat Read"},
    {"code": "chat:write", "name": "Chat Write"},
    {"code": "settings:write", "name": "Settings Write"},
    {"code": "admin:access", "name": "Admin Access"},
    {"code": "admin:users:manage", "name": "Manage Users"},
    {"code": "admin:roles:manage", "name": "Manage Roles"},
]

DEFAULT_ROLE_PERMISSIONS = {
    DEFAULT_ROLE_USER: ["chat:read", "chat:write", "settings:write"],
    DEFAULT_ROLE_ADMIN: [item["code"] for item in DEFAULT_PERMISSIONS],
}


def build_user_context(user) -> dict[str, Any]:
    role = getattr(user, "role", None)
    permissions = []
    if role is not None:
        permissions = sorted(
            {
                permission.code
                for permission in getattr(role, "permissions", [])
                if permission and permission.code
            }
        )
    return {
        "user_id": user.id,
        "username": user.username,
        "role": role.name if role else DEFAULT_ROLE_USER,
        "permissions": permissions,
    }


def sync_default_rbac(db):
    created_permissions: dict[str, int] = {}
    for definition in DEFAULT_PERMISSIONS:
        permission = get_permission_by_code(db, definition["code"])
        if not permission:
            permission = create_permission(
                db,
                definition["code"],
                definition["name"],
            )
        created_permissions[permission.code] = permission.id

    for role_name, permission_codes in DEFAULT_ROLE_PERMISSIONS.items():
        role = get_role_by_name(db, role_name)
        if not role:
            role = create_role(
                db,
                role_name,
                description=f"Default {role_name} role",
                is_system=True,
            )

        permission_ids = [
            created_permissions[code]
            for code in permission_codes
            if code in created_permissions
        ]
        replace_role_permissions(db, role.id, permission_ids)


def get_default_role(db):
    sync_default_rbac(db)
    role = get_role_by_name(db, DEFAULT_ROLE_USER)
    if not role:
        raise RuntimeError("default role not initialized")
    return role


def get_admin_role(db):
    sync_default_rbac(db)
    role = get_role_by_name(db, DEFAULT_ROLE_ADMIN)
    if not role:
        raise RuntimeError("admin role not initialized")
    return role


def get_accessible_permissions(db, user) -> set[str]:
    role = getattr(user, "role", None)
    if not role:
        return set()
    return {
        permission.code
        for permission in getattr(role, "permissions", [])
        if permission and permission.code
    }


def require_permissions(*permissions: str):
    required = set(permissions)

    from .auth import get_current_user

    def dependency(current_user: dict[str, Any] = Depends(get_current_user)):
        user_permissions = set(current_user.get("permissions", []))
        if not required.issubset(user_permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="INSUFFICIENT PERMISSIONS",
            )
        return current_user

    return dependency


def require_roles(*roles: str):
    required = set(roles)

    from .auth import get_current_user

    def dependency(current_user: dict[str, Any] = Depends(get_current_user)):
        if current_user.get("role") not in required:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="INSUFFICIENT ROLE",
            )
        return current_user

    return dependency


def list_roles_with_permissions(db):
    roles = get_roles(db)

    result = []
    for role in roles:
        role_permissions = [
            {
                "permission_id": permission.id,
                "code": permission.code,
                "name": permission.name,
                "description": permission.description,
            }
            for permission in getattr(role, "permissions", [])
            if permission
        ]
        result.append(
            {
                "role_id": role.id,
                "name": role.name,
                "description": role.description,
                "permissions": role_permissions,
            }
        )
    return result


def list_admin_users(db):
    users = get_users_with_roles(db)
    return [build_user_context(user) for user in users]


def assign_role_to_user(db, user, role):
    user.role_id = role.id
    db.commit()
    db.refresh(user)
    return user


def set_role_permissions(db, role_id: int, permission_codes: Iterable[str]):
    permissions = []
    for code in permission_codes:
        permission = get_permission_by_code(db, code)
        if not permission:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown permission: {code}",
            )
        permissions.append(permission)
    replace_role_permissions(db, role_id, [permission.id for permission in permissions])


def get_role_permissions(db, role_id: int):
    role = get_role_by_id(db, role_id)
    if not role:
        return None
    return [
        {
            "permission_id": permission.id,
            "code": permission.code,
            "name": permission.name,
            "description": permission.description,
        }
        for permission in getattr(role, "permissions", [])
        if permission
    ]


def ensure_bootstrap_admin(db, username: str | None, password: str | None):
    if not username or not password:
        return None

    sync_default_rbac(db)
    user = get_user_by_username(db, username.strip())
    admin_role = get_admin_role(db)
    if user:
        user.role_id = admin_role.id
        user.password = hash_password(password.strip())
        db.commit()
        db.refresh(user)
        return user

    created = create_user(
        db,
        username.strip(),
        hash_password(password.strip()),
        role_id=admin_role.id,
    )
    return created
