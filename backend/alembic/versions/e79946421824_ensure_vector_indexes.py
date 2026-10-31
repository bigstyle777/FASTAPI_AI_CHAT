"""ensure_vector_indexes

补建 ivfflat 向量索引。历史迁移（b7c1d4e93f2a / d3b2a1f7c9e8）已声明
这两个索引，但部分已部署环境可能因当时 pgvector 扩展未就绪、或空表建
索引被跳过而缺失，导致向量检索退化为全表扫描。

用 CREATE INDEX IF NOT EXISTS 幂等补建：索引已存在的环境是空操作，
缺失的环境补上。downgrade 不 DROP（索引由各自原始迁移的 downgrade 负责）。

Revision ID: e79946421824
Revises: d9e2b4f6a8c0
Create Date: 2026-10-31 22:09:55.748507

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e79946421824'
down_revision: Union[str, Sequence[str], None] = 'd9e2b4f6a8c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """幂等补建向量索引。"""
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_memories_embedding_ivfflat "
        "ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_rag_chunk_embeddings_embedding_ivfflat "
        "ON rag_chunk_embeddings USING ivfflat (embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    """不删除索引：由各自原始迁移的 downgrade 负责。"""
    pass
