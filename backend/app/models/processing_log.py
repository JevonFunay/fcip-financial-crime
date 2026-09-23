import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base

# FR-105 event codes. Registered names, not free text, so the log can be
# filtered and reconciled instead of only read.
EVENT_BATCH_REGISTERED = "BATCH_REGISTERED"
EVENT_VALIDATION_COMPLETED = "VALIDATION_COMPLETED"
EVENT_RECONCILIATION_OK = "RECONCILIATION_OK"
EVENT_RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
EVENT_BATCH_FAILED = "BATCH_FAILED"


class ProcessingLog(Base):
    """FR-105: a per-batch log of what the platform did with the file, so a
    gap is explicit rather than inferred from missing rows."""

    __tablename__ = "processing_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Insertion order. created_at alone cannot order these: Postgres `now()` is
    # transaction start time, so every entry written in one transaction shares
    # a timestamp and the log would fall back to an arbitrary order.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    ingestion_batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_batch.id"), nullable=False, index=True
    )
    event_code: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(String, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
