"""Memory 数据模型：单表 memories，用 type 列区分 semantic / episodic。

为什么是一张表
--------------
两种记忆的检索方式不同（semantic 靠 key 精确匹配，episodic 靠向量），
但它们的生命周期管理（Decay / Consolidation / Reinforcement）是同一套。
拆成两张表的话，Phase 4-6 要给两张表各写一遍生命周期逻辑，所以合并，
用 type 列区分。代价是两种记忆各自有一半字段为 NULL，可以接受。

Embedding 不是 Memory 本身
--------------------------
它只是 retrieval 的一种索引手段，所以是 nullable 的列：
semantic 默认不写向量（靠 key 命中），episodic 才依赖向量检索。
"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..core.config import settings
from ..core.database import Base


class Memory(Base):
    """一条记忆：稳定事实（semantic）或过往事件（episodic）。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    # 产生这条记忆的会话。semantic 也可以为空（跨会话的长期事实）。
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_sessions.id"),
        nullable=True,
    )

    # 判别列：semantic | episodic
    type: Mapped[str] = mapped_column(String(20), nullable=False)

    # ---- semantic 专用：key 是 UPSERT 的锚点 ----
    key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ---- episodic 专用：summary 同时是被向量化的文本 ----
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_type: Mapped[str | None] = mapped_column(String(40), nullable=True)

    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    source: Mapped[str] = mapped_column(
        String(40),
        default="llm_extraction",
        nullable=False,
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(settings.rag_embedding_dimension),
        nullable=True,
    )
    # 列名是 metadata，但 DeclarativeBase 自带 .metadata 属性，
    # 属性名只能改成 metadata_ 避开冲突。
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata",
        JSON,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    # ---------------------------------------------------------------
    # 以下四列是 Phase 4-6（Consolidation / Decay / Reinforcement）的
    # 预留接口：本次只建列，不写任何读写逻辑，避免出现"改了一半"的行为。
    # ---------------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )
    access_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consolidated_into_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("ix_memories_user_id_type", "user_id", "type"),
        Index("ix_memories_session_id", "session_id"),
        # semantic 的 UPSERT 锚点。必须是部分索引：episodic 的 key 恒为
        # NULL，普通唯一索引对多个 NULL 不生效，约束不住 semantic。
        Index(
            "uq_memories_semantic_key",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("type = 'semantic'"),
        ),
    )

    @property
    def content(self) -> str:
        """这条记忆的可读正文：semantic 取 value，episodic 取 summary。"""
        return self.value if self.type == "semantic" else (self.summary or "")
