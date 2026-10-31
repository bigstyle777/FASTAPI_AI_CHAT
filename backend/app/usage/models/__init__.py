"""LLM 调用成本账本。

一次模型调用一行（含失败），金额按 core/model_pricing.py 的价目表折算成
整数微元。它是对账的权威来源；Redis 里的日计数只是"事前闸门"的快照。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ...core.database import Base


class LlmUsageLog(Base):
    __tablename__ = "llm_usage_logs"
    __table_args__ = (
        Index("ix_llm_usage_logs_user_created", "user_id", "created_at"),
        Index("ix_llm_usage_logs_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    session_id: Mapped[int | None] = mapped_column(
        ForeignKey("chat_sessions.id"), nullable=True
    )
    # chat / agent / title / memory / embedding
    scene: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # success / failed / cancelled / fallback
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    cost_micros: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
