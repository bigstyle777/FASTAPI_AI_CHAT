"""Admin（RBAC）领域数据访问层。"""

from .rbac import (
    add_permission_to_role,
    create_permission,
    create_role,
    get_permission_by_code,
    get_permission_by_id,
    get_permissions,
    get_role_by_id,
    get_role_by_name,
    get_roles,
    replace_role_permissions,
)

__all__ = [
    "add_permission_to_role",
    "create_permission",
    "create_role",
    "get_permission_by_code",
    "get_permission_by_id",
    "get_permissions",
    "get_role_by_id",
    "get_role_by_name",
    "get_roles",
    "replace_role_permissions",
]