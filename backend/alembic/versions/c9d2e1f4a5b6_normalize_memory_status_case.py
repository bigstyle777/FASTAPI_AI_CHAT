"""normalize memories.status to lowercase

历史上 memories.status 混用大小写：默认列值是 "active"（小写），而
Phase 5-6 引入的 REINFORCED / CONSOLIDATED / DECAYING / ARCHIVED 被
status.py 写成大写，明文写进了库。这会让 repository 里以常量为条件的
SQL（如 status NOT IN (ARCHIVED, CONSOLIDATED)）漏过滤存量的大写行。

方案 A 统一为全小写（与列默认 "active" 一致、与 PostgreSQL 小写标识符
惯例一致）。本迁移把存量行归一化为小写。

Revision ID: c9d2e1f4a5b6
Revises: b7c1d4e93f2a
Create Date: 2026-09-04 19:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c9d2e1f4a5b6"
down_revision: Union[str, Sequence[str], None] = "b7c1d4e93f2a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """把存量 memories.status 统一归一化为小写。"""
    op.execute(
        "UPDATE memories SET status = lower(status)"
        " WHERE status <> lower(status)"
    )


def downgrade() -> None:
    """还原为历史大小写（"active" 保持小写）。

    upgrade 是幂等的 lower()，downgrade 只能把已知的四种大写状态还原；
    无法知晓某行原本是否真为大写，但这是可还原边界内最优。
    """
    op.execute(
        """
        UPDATE memories SET status = CASE status
            WHEN 'reinforced' THEN 'REINFORCED'
            WHEN 'consolidated' THEN 'CONSOLIDATED'
            WHEN 'decaying' THEN 'DECAYING'
            WHEN 'archived' THEN 'ARCHIVED'
            ELSE status
        END
        WHERE status IN ('reinforced', 'consolidated', 'decaying', 'archived')
        """
    )