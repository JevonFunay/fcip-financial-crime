import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Merchant(Base):
    """A merchant outlet of a business customer (TRD §6.1 merchants.csv): what
    P10 judges a merchant against, its category (MCC) and declared hours."""

    __tablename__ = "merchant"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    merchant_ref: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    # The business that owns the outlet.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customer.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    mcc: Mapped[str] = mapped_column(String(4), nullable=False)
    declared_ticket_band: Mapped[str | None] = mapped_column(String, nullable=True)
    # "HH:MM-HH:MM" as declared; "00:00-23:59" means always open.
    operating_hours: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
