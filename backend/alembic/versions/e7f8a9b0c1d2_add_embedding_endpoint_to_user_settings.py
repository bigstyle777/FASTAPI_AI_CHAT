"""add embedding endpoint fields to user_settings

Revision ID: e7f8a9b0c1d2
Revises: a1b2c3d4e5f6
Create Date: 2026-08-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_settings",
        sa.Column("embedding_base_url", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "user_settings",
        sa.Column("embedding_model", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_settings", "embedding_model")
    op.drop_column("user_settings", "embedding_base_url")
