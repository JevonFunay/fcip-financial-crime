import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Session(Base):
    """Server-side refresh token record (TRD §12.2).

    Refresh tokens are rotated on every use and tracked here so they can be
    revoked; the raw token is never stored, only its hash.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    refresh_token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Absolute timeout (created_at + 8h per TRD §12.2).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Bumped on every use; compared against the 30-minute idle timeout.
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
