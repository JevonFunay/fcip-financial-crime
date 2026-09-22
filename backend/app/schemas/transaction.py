import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from app.models.enums import TransactionDirection


class TransactionOut(BaseModel):
    id: uuid.UUID
    transaction_ref: str
    account_number: str
    transaction_date: datetime
    amount: Decimal
    currency: str
    direction: TransactionDirection
    channel: str
    counterparty_ref: str | None
    description: str | None


class TransactionRow(TransactionOut):
    """A transaction as shown in the browse table, with its owning customer."""

    customer_id: uuid.UUID
    customer_ref: str
    customer_name: str


class TransactionListResponse(BaseModel):
    items: list[TransactionRow]
    total: int
    limit: int
    offset: int
