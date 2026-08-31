from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password: Mapped[str] = mapped_column(String, nullable=False)
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False)

    sessions: Mapped[list["ChatSession"]] = relationship(  # noqa: F821
        back_populates="user"
    )
    settings: Mapped["UserSetting | None"] = relationship(
        back_populates="user",
        uselist=False,
    )
    role: Mapped["Role"] = relationship(back_populates="users")  # noqa: F821
    rag_documents: Mapped[list["RagDocument"]] = relationship(  # noqa: F821
        back_populates="user"
    )


class UserSetting(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"),
        unique=True,
        nullable=False,
    )
    api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    provider: Mapped[str] = mapped_column(String, default="deepseek")
    # 用户独立的 RAG embedding key；为空时回退到系统 .env 的 RAG_EMBEDDING_API_KEY
    embedding_api_key: Mapped[str | None] = mapped_column(String, nullable=True)
    embedding_base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    user: Mapped["User"] = relationship(back_populates="settings")
