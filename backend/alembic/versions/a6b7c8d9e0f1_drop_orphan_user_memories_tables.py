"""drop orphaned user_memories / user_memory_embeddings tables

这两张表由链中段的 e5bc1ceee72c / f2a9c1b3d4e5 创建，但全仓库（models /
repository / schemas / 任何业务代码）都不再引用它们 —— 是早期"user memory"
设计在后来统一为单表 `memories`（b7c1d4e93f2a）后留下的孤儿表。删除不会触碰
活跃的 `memories` 表。本次放在链头执行 DROP；downgrade 按原 schema 重建，保证可逆。

Revision ID: a6b7c8d9e0f1
Revises: c9d2e1f4a5b6
Create Date: 2026-09-04 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, Sequence[str], None] = "c9d2e1f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Drop 两张孤儿表。先删子表（外键），再删父表。"""
    op.drop_table("user_memory_embeddings")
    op.drop_table("user_memories")


def downgrade() -> None:
    """按原 schema 重建（复用 e5bc1ceee72c / f2a9c1b3d4e5 的定义）。"""
    op.create_table(
        "user_memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_memories_user_id"),
        "user_memories",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "user_memory_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("memory_id", sa.Integer(), nullable=False),
        sa.Column("sentence_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_id"],
            ["user_memories.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id", "sentence_index"),
    )
    op.create_index(
        op.f("ix_user_memory_embeddings_memory_id"),
        "user_memory_embeddings",
        ["memory_id"],
        unique=False,
    )