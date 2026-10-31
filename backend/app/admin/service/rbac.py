from __future__ import annotations

import logging
from typing import Any, Iterable

from fastapi import Depends, HTTPException, status

logger = logging.getLogger(__name__)

from ...core.security import hash_password
from ...user.repository import (
    create_user,
    get_user_by_username,
    get_users_with_roles,
)
from ..repository import (
    create_permission,
    create_role,
    get_permission_by_code,
    get_role_by_id,
    get_role_by_name,
    get_roles,
    replace_role_permissions,
)

# 默认引导管理员密码（不安全，生产环境必须修改）
_DEFAULT_BOOTSTRAP_PASSWORD = "admin123456"

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
    role = get_role_by_name(db, DEFAULT_ROLE_USER)
    if not role:
        # 只有角色确实缺失时才重建（正常路径由启动时的 lifespan 同步过）
        sync_default_rbac(db)
        role = get_role_by_name(db, DEFAULT_ROLE_USER)
    if not role:
        raise RuntimeError("default role not initialized")
    return role


def get_admin_role(db):
    role = get_role_by_name(db, DEFAULT_ROLE_ADMIN)
    if not role:
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

    from ...user.services.auth import get_current_user

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

    from ...user.services.auth import get_current_user

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
    """首次启动时创建引导管理员；之后的启动不做任何重置。

    只在用户不存在时创建。已存在的同名用户（无论角色是什么）不会被
    改密码、不会被提权——否则：
    1. 管理员改过的密码每次重启都被 .env 覆盖回去，等于永久后门；
    2. 任何人抢先注册同名账号，等一次重启就能白拿 admin。

    忘记密码的恢复方式：改 .env 里的用户名（或直接删库重建）。
    """
    if not username or not password:
        return None

    sync_default_rbac(db)
    user = get_user_by_username(db, username.strip())
    if user:
        admin_role = get_role_by_name(db, DEFAULT_ROLE_ADMIN)
        if admin_role is not None and user.role_id != admin_role.id:
            logger.warning(
                "[安全警告] 已存在同名用户 %r 但不是 admin，本次启动不自动提权。"
                "如确需授权，请由现有管理员在后台手动调整角色。",
                username,
            )
        return user

    if password.strip() == _DEFAULT_BOOTSTRAP_PASSWORD:
        logger.warning(
            "[安全警告] 引导管理员使用了默认密码 'admin123456'，"
            "请尽快在 .env 中修改 BOOTSTRAP_ADMIN_PASSWORD 或登录后更改密码。"
        )

    admin_role = get_admin_role(db)
    created = create_user(
        db,
        username.strip(),
        hash_password(password.strip()),
        role_id=admin_role.id,
    )
    logger.info("已创建引导管理员账号 %r", username)
    return created
