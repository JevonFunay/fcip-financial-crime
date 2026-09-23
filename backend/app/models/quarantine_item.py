import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class QuarantineItem(Base):
    __tablename__ = "quarantine_item"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    source_file_name: Mapped[str] = mapped_column(String, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_row: Mapped[dict] = mapped_column(JSONB, nullable=False)
    error_reason: Mapped[str] = mapped_column(String, nullable=False)
    # See Transaction.ingestion_batch_id — nullable for the same reason.
    ingestion_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_batch.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
