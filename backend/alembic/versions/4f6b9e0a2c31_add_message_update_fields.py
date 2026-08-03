"""add message update fields

Revision ID: 4f6b9e0a2c31
Revises: a944a9709801
Create Date: 2026-08-02 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4f6b9e0a2c31"
down_revision: Union[str, Sequence[str], None] = "a944a9709801"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"]: column for column in inspector.get_columns("messages")}

    if "updated_at" not in columns:
        op.add_column("messages", sa.Column("updated_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE messages SET updated_at = created_at WHERE updated_at IS NULL")

    add_parent_id = "parent_id" not in columns
    tighten_updated_at = columns.get("updated_at", {}).get("nullable", True)

    if add_parent_id or tighten_updated_at:
        with op.batch_alter_table("messages") as batch_op:
            if tighten_updated_at:
                batch_op.alter_column(
                    "updated_at",
                    existing_type=sa.DateTime(),
                    nullable=False,
                )

            if add_parent_id:
                batch_op.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_messages_parent_id_messages",
                    "messages",
                    ["parent_id"],
                    ["id"],
                )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("messages")}

    with op.batch_alter_table("messages") as batch_op:
        if "parent_id" in columns:
            batch_op.drop_constraint(
                "fk_messages_parent_id_messages",
                type_="foreignkey",
            )
            batch_op.drop_column("parent_id")
        if "updated_at" in columns:
            batch_op.drop_column("updated_at")
