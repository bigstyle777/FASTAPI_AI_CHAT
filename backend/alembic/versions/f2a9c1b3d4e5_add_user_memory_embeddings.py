"""add user memory embeddings

Revision ID: f2a9c1b3d4e5
Revises: e5bc1ceee72c
Create Date: 2026-08-15 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "f2a9c1b3d4e5"
down_revision: Union[str, Sequence[str], None] = "e5bc1ceee72c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
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
        "ix_user_memory_embeddings_memory_id",
        "user_memory_embeddings",
        ["memory_id"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_user_memory_embeddings_memory_id",
        table_name="user_memory_embeddings",
    )
    op.drop_table("user_memory_embeddings")
