from uuid import UUID
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    username_normalized: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(220))

    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")
    sessions: Mapped[list["UserSession"]] = relationship(back_populates="user")


class UserSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "user_sessions"

    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="sessions")


class AuthRateLimit(Base):
    __tablename__ = "auth_rate_limits"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    count: Mapped[int] = mapped_column(Integer)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
