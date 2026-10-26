"""Evidence attached to a case (FR-708, FR-811–813).

Evidence is insert-only (TRD ADR-004 / FR-813): the DB trigger in migration
0010 enforces this at the row level.  Content-addressed storage (MinIO /
FR-811) is deferred; for now the file is stored in the database as a URI /
server-side path reference. The `file_hash` (SHA-256) gives integrity without
needing MinIO.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import EvidenceType


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # FR-811: evidence belongs to a case.
    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case.id"), nullable=False, index=True
    )
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    # Correlation chain: same as the case's alert chain so the evidence
    # appears in the UAT-12 audit trail.
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)

    evidence_type: Mapped[EvidenceType] = mapped_column(
        SAEnum(EvidenceType, name="evidence_type"), nullable=False
    )
    # Original file name supplied by the uploader (display only).
    file_name: Mapped[str] = mapped_column(String, nullable=False)
    # FR-813: file_hash is the SHA-256 hex digest of the uploaded content.
    file_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Stored path / URI.  NULL for NOTE type (text only).
    storage_uri: Mapped[str | None] = mapped_column(String, nullable=True)
    # Description / note text.  Required for NOTE type; optional for others.
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
