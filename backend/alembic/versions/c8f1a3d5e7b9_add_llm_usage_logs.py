"""add llm_usage_logs table

模型调用成本账本：每次调用一行，token 与折算后的金额（整数微元）。
账本是权威数据，Redis 日计数只做事前闸门。

Revision ID: c8f1a3d5e7b9
Revises: f3a8c1e5b7d2
Create Date: 2026-10-31

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8f1a3d5e7b9"
down_revision: Union[str, Sequence[str], None] = "f3a8c1e5b7d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "llm_usage_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("scene", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_micros", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_usage_logs_user_created",
        "llm_usage_logs",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_llm_usage_logs_created",
        "llm_usage_logs",
        ["created_at"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_llm_usage_logs_created", table_name="llm_usage_logs")
    op.drop_index("ix_llm_usage_logs_user_created", table_name="llm_usage_logs")
    op.drop_table("llm_usage_logs")
