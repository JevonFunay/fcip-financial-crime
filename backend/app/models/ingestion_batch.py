import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import IngestionBatchStatus


class IngestionBatch(Base):
    """FR-101: every ingestion runs inside a registered batch, so a file can be
    traced back to who loaded it, when, for which business date, and what the
    file actually contained (checksum)."""

    __tablename__ = "ingestion_batch"
    __table_args__ = (
        # FR-101 / TRD C-01: reject a duplicate checksum for the same source
        # system and business date. Registering the same file under a different
        # business date is legitimate (a genuine re-send), so the constraint is
        # scoped rather than global on the checksum.
        UniqueConstraint(
            "source_system", "business_date", "file_checksum", name="uq_batch_source_business_date_checksum"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    batch_ref: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String, nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    file_name: Mapped[str] = mapped_column(String, nullable=False)
    file_checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # What the sender claims the file holds. Null when not declared; when it is
    # declared, the end-of-batch reconciliation checks it (FR-105).
    expected_records: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[IngestionBatchStatus] = mapped_column(
        SAEnum(IngestionBatchStatus, name="ingestion_batch_status"),
        nullable=False,
        default=IngestionBatchStatus.REGISTERED,
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quarantined_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Chains this batch's own audit events (registered -> completed/failed).
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    registered_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
