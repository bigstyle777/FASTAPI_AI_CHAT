"""add rbac tables

Revision ID: c4e7d9a8f2b1
Revises: 074c4c93201a
Create Date: 2026-08-08 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c4e7d9a8f2b1"
down_revision: Union[str, Sequence[str], None] = "074c4c93201a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "roles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "permissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"]),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.add_column(
        "users",
        sa.Column("role_id", sa.Integer(), nullable=True),
    )

    roles = sa.table(
        "roles",
        sa.column("id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("is_system", sa.Boolean()),
    )
    permissions = sa.table(
        "permissions",
        sa.column("id", sa.Integer()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
    )
    role_permissions = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Integer()),
        sa.column("permission_id", sa.Integer()),
    )
    users = sa.table(
        "users",
        sa.column("id", sa.Integer()),
        sa.column("role_id", sa.Integer()),
    )

    conn = op.get_bind()
    permission_rows = [
        ("chat:read", "Chat Read"),
        ("chat:write", "Chat Write"),
        ("settings:write", "Settings Write"),
        ("admin:access", "Admin Access"),
        ("admin:users:manage", "Manage Users"),
        ("admin:roles:manage", "Manage Roles"),
    ]
    conn.execute(
        permissions.insert(),
        [
            {"code": code, "name": name, "description": None}
            for code, name in permission_rows
        ],
    )

    conn.execute(
        roles.insert(),
        [
            {"name": "user", "description": "Default user role", "is_system": True},
            {"name": "admin", "description": "System administrator", "is_system": True},
        ],
    )

    role_result = conn.execute(sa.select(roles.c.id, roles.c.name))
    role_ids = {row.name: row.id for row in role_result}

    permission_result = conn.execute(sa.select(permissions.c.id, permissions.c.code))
    permission_ids = {row.code: row.id for row in permission_result}

    conn.execute(
        role_permissions.insert(),
        [
            {"role_id": role_ids["user"], "permission_id": permission_ids["chat:read"]},
            {"role_id": role_ids["user"], "permission_id": permission_ids["chat:write"]},
            {"role_id": role_ids["user"], "permission_id": permission_ids["settings:write"]},
            {"role_id": role_ids["admin"], "permission_id": permission_ids["chat:read"]},
            {"role_id": role_ids["admin"], "permission_id": permission_ids["chat:write"]},
            {"role_id": role_ids["admin"], "permission_id": permission_ids["settings:write"]},
            {"role_id": role_ids["admin"], "permission_id": permission_ids["admin:access"]},
            {"role_id": role_ids["admin"], "permission_id": permission_ids["admin:users:manage"]},
            {"role_id": role_ids["admin"], "permission_id": permission_ids["admin:roles:manage"]},
        ],
    )

    conn.execute(
        users.update().values(role_id=role_ids["user"])
    )

    op.alter_column(
        "users",
        "role_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_users_role_id_roles",
        "users",
        "roles",
        ["role_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_role_id_roles", "users", type_="foreignkey")
    op.drop_column("users", "role_id")
    op.drop_table("role_permissions")
    op.drop_table("permissions")
    op.drop_table("roles")
