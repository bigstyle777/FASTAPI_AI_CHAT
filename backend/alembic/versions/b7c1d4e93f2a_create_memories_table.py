"""create memories table (semantic / episodic)

Memory System Phase 1-3 MVP：单表 + type 判别列。

除本次要用的字段外，还建了 status / access_count / expires_at /
consolidated_into_id 四个列，供 Phase 4-6（Consolidation、Decay、
Reinforcement）使用 —— 本次只建列，不写任何读写逻辑。

Revision ID: b7c1d4e93f2a
Revises: a3b4c5d6e7f8
Create Date: 2026-09-04 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = "b7c1d4e93f2a"
down_revision: Union[str, Sequence[str], None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        # 判别列：semantic（稳定事实/偏好）/ episodic（过去的事件/决策）
        sa.Column("type", sa.String(length=20), nullable=False),
        # semantic 用：key 是 UPSERT 的锚点，value 是事实内容
        sa.Column("key", sa.String(length=120), nullable=True),
        sa.Column("value", sa.Text(), nullable=True),
        # episodic 用：summary 是事件摘要，也是被向量化的文本
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=True),
        sa.Column("importance", sa.Float(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        # semantic 默认不写向量（靠 key 精确匹配），所以可为 NULL
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_accessed_at", sa.DateTime(), nullable=True),
        # --- 以下四列 Phase 4-6 才用，本次只建列 ---
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("access_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("consolidated_into_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_memories_user_id", "memories", ["user_id"])
    op.create_index("ix_memories_session_id", "memories", ["session_id"])
    op.create_index("ix_memories_user_id_type", "memories", ["user_id", "type"])

    # semantic 的 UPSERT 锚点。必须是部分索引：episodic 行的 key 恒为 NULL，
    # 普通唯一索引下多个 NULL 互不冲突，但语义上也起不到约束作用，
    # 而且会把 episodic 行一起卷进唯一性判断。
    op.create_index(
        "uq_memories_semantic_key",
        "memories",
        ["user_id", "key"],
        unique=True,
        postgresql_where=sa.text("type = 'semantic'"),
    )

    op.create_index(
        "ix_memories_embedding_ivfflat",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"lists": "100"},
        postgresql_using="ivfflat",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_memories_embedding_ivfflat",
        table_name="memories",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_index("uq_memories_semantic_key", table_name="memories")
    op.drop_index("ix_memories_user_id_type", table_name="memories")
    op.drop_index("ix_memories_session_id", table_name="memories")
    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_table("memories")
