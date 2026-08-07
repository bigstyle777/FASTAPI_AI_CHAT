"""add branch anchor to chat sessions

Revision ID: 92f63a1b0d4c
Revises: 6656070d39ea
Create Date: 2026-08-05 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "92f63a1b0d4c"
down_revision: Union[str, Sequence[str], None] = "6656070d39ea"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.add_column(
            sa.Column("branch_from_message_id", sa.Integer(), nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_chat_sessions_branch_from_message_id_messages",
            "messages",
            ["branch_from_message_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.drop_constraint(
            "fk_chat_sessions_branch_from_message_id_messages",
            type_="foreignkey",
        )
        batch_op.drop_column("branch_from_message_id")
