import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import AlertStatus


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    pattern_code: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[AlertStatus] = mapped_column(
        SAEnum(AlertStatus, name="alert_status"), nullable=False, default=AlertStatus.OPEN
    )
    # Shared by every audit_log row in this alert's chain (creation ->
    # disposition -> case), per FRD §5.0.
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    # Entity resolution isn't built yet in this skeleton, so customer_id is a
    # temporary stand-in for the FRD's entity_id concept — see README.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer.id"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(String, nullable=False)
    # Pattern-specific evidence (e.g. P02: TXN_COUNT_IN_BAND, AGGREGATE_AMOUNT,
    # THRESHOLD_APPLIED, BAND_LOWER_APPLIED, AMOUNT_DISPERSION_CV,
    # DISTINCT_ACCOUNTS, DISTINCT_COUNTERPARTIES, CHANNEL_MIX, window_start,
    # window_end) per FRD §8.2 and the FR-307 reproducibility requirement.
    detection_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    case_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("case.id"), nullable=True)
    disposed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    disposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disposition_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AlertTransaction(Base):
    """Join table: which transactions are the evidence behind an alert."""

    __tablename__ = "alert_transaction"

    alert_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("alert.id"), primary_key=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("transaction.id"), primary_key=True
    )
