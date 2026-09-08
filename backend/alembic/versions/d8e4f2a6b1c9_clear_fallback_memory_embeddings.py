"""clear fallback (hash-noise) embeddings from memories

历史上 app/rag/embedding.embed_texts 在没配 API Key 时返回 sha256 派生的
伪向量，记忆模块把它当真向量写进了 memories.embedding。这种行既不报错也
无法自愈：embedding 不为 NULL，backfill_missing_embeddings（只找 NULL）
永远跳过它，而 vector_search 已经在拿噪声当相似度排序。

写入侧现已改为 allow_fallback=False（拿不到真向量就存 NULL，等回填），
本次把存量污染行一次性置回 NULL，之后由既有的回填任务补上真向量。

判定靠数值特征，不依赖原文：伪向量每个分量都是 (byte / 255) * 2 - 1，
落在 (2k - 255) / 255 这张离散网格上，真实模型输出不可能全部命中。
与 app/rag/embedding.looks_like_fallback_vector 保持同一套判据（迁移
自包含，不 import 应用代码）。

Revision ID: d8e4f2a6b1c9
Revises: a6b7c8d9e0f1
Create Date: 2026-09-07 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "d8e4f2a6b1c9"
down_revision: Union[str, Sequence[str], None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# float4 往返的精度损失在 1e-5 量级，容差放到 1e-3 足够宽松又不会误伤
# 真实向量（真实分量要全部落在网格上才算命中，概率为零）。
_GRID_TOLERANCE = 1e-3


def _is_fallback_vector(values: list[float]) -> bool:
    """该向量的每个分量是否都落在伪向量的离散网格上。"""
    if not values:
        return False
    for value in values:
        scaled = (value + 1.0) * 255.0 / 2.0
        if abs(scaled - round(scaled)) > _GRID_TOLERANCE:
            return False
    return True


def _parse_vector(raw) -> list[float]:
    """pgvector 的 text 输出形如 "[0.1,-0.2,...]"，解析成分量列表。"""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [float(value) for value in raw]
    text = str(raw).strip()
    if not text or text in ("NULL", "[]"):
        return []
    return [float(part) for part in text.strip("[]").split(",") if part.strip()]


def upgrade() -> None:
    """把伪向量行置回 NULL，交给回填任务补真向量。"""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "memories" not in inspector.get_table_names():
        return
    if "embedding" not in {
        column["name"] for column in inspector.get_columns("memories")
    }:
        return

    rows = bind.execute(
        sa.text(
            "SELECT id, embedding::text FROM memories WHERE embedding IS NOT NULL"
        )
    ).fetchall()

    polluted = [row[0] for row in rows if _is_fallback_vector(_parse_vector(row[1]))]
    if not polluted:
        return

    # 分批 UPDATE，避免一次锁太多行
    clear_stmt = sa.text(
        "UPDATE memories SET embedding = NULL WHERE id IN :ids"
    ).bindparams(sa.bindparam("ids", expanding=True))
    for start in range(0, len(polluted), 500):
        bind.execute(clear_stmt, {"ids": polluted[start : start + 500]})
    print(f"cleared {len(polluted)} fallback memory embeddings")


def downgrade() -> None:
    """不可逆：本次是数据修复，重建哈希伪向量只会把污染加回去。

    置 NULL 的行由 backfill_missing_embeddings 在 embedding 服务可用时
    补上真向量 —— 那才是这些行本该有的内容。
    """
