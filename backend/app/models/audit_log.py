import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Shared across an entire chain of events (e.g. detection run -> alert
    # created -> disposition -> case created) so it can be traced end to end,
    # per FRD §5.0.
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    # Nullable: some transitions (e.g. a detection job creating an alert) have
    # no human actor.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    actor_role: Mapped[str | None] = mapped_column(String, nullable=True)

    object_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    from_state: Mapped[str | None] = mapped_column(String, nullable=True)
    to_state: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # TRD §8.8 `action` (e.g. RULE_VERSION_CREATED) and structured detail such
    # as a parameter-level diff (the TRD's before/after values). Null on events
    # that predate these columns, whose meaning is carried by the transition.
    action: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
