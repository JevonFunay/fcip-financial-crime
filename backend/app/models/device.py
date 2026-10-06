import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class Device(Base):
    """Device master data (TRD §6.1 devices.csv). Who used a device, and when,
    comes from the transactions; this holds what the source says about it."""

    __tablename__ = "device"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    device_ref: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    device_type: Mapped[str | None] = mapped_column(String, nullable=True)
    is_emulator: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_rooted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
