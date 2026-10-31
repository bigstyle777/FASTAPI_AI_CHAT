"""add agent_runs.idempotency_key

Agent 运行的幂等键：同一请求重复提交时只执行一次。历史记录没有键，
因此列可空；唯一约束保证并发下的最终一致性。

Revision ID: d9e2b4f6a8c0
Revises: c8f1a3d5e7b9
Create Date: 2026-10-31

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9e2b4f6a8c0"
down_revision: Union[str, Sequence[str], None] = "c8f1a3d5e7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "agent_runs",
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_agent_runs_idempotency_key",
        "agent_runs",
        ["idempotency_key"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(
        "uq_agent_runs_idempotency_key",
        "agent_runs",
        type_="unique",
    )
    op.drop_column("agent_runs", "idempotency_key")
