import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base
from app.models.enums import TransactionDirection


class Transaction(Base):
    __tablename__ = "transaction"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    transaction_ref: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("account.id"), nullable=False, index=True
    )
    transaction_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    direction: Mapped[TransactionDirection] = mapped_column(
        SAEnum(TransactionDirection, name="transaction_direction"), nullable=False
    )
    # No channel restriction for P02 aggregation per FRD §8.2 — stored for
    # CHANNEL_MIX reporting in alert.detection_details, not for filtering.
    channel: Mapped[str] = mapped_column(String, nullable=False)
    counterparty_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    # The rest of the TRD §6.1 transaction contract that detection needs
    # (migration 0007). All optional, so a file without them still loads; the
    # Fraud model cannot judge a device it is not told about, so a row without
    # one is scored with its device features unknown, like a source row that
    # lacks it (TRD §11.4 missing_device).
    transaction_type: Mapped[str | None] = mapped_column(String, nullable=True)
    counterparty_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    merchant_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    device_ref: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # status_from_source (e.g. REVERSED), for P10's refund rate.
    source_status: Mapped[str | None] = mapped_column(String, nullable=True)
    # Source observations, not inferred customer-master facts (behavioral_v1).
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    login_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transaction_duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    account_balance: Mapped[float | None] = mapped_column(Float, nullable=True)
    customer_age: Mapped[float | None] = mapped_column(Float, nullable=True)
    customer_occupation: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp_precision: Mapped[str | None] = mapped_column(String(6), nullable=True)
    source_timezone_known: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Which registered batch loaded this row (FR-101). Nullable because rows
    # created by the seed script and by direct service calls in tests have no
    # batch behind them.
    ingestion_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_batch.id"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
