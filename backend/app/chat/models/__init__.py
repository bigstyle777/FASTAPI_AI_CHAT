from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ...core.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    parent_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True
    )
    branch_from_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    is_pinned: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    user: Mapped["User"] = relationship(back_populates="sessions")  # noqa: F821
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        foreign_keys="Message.session_id",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # token字段
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )

    session: Mapped["ChatSession"] = relationship(
        back_populates="messages",
        foreign_keys=[session_id],
    )


class ChatGeneration(Base):
    """一次流式生成任务的全生命周期记录。

    状态机与流转约束见 chat/services/generation.py；权威数据源是本表，
    Redis 里的 stop_requested 只是"停止信号"，不是状态。
    """

    __tablename__ = "chat_generations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # 本轮生成对应的用户消息；改消息重生成等路径可空
    user_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id"), nullable=True
    )
    # 幂等键：同一用户意图（双击/重试/刷新重发）只生成一次。可空兼容无键路径
    idempotency_key: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)