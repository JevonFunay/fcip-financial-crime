from datetime import datetime

from pydantic import BaseModel


class OverviewSummary(BaseModel):
    """Counts behind the landing page tiles — one call instead of four list queries."""

    customers: int
    accounts: int
    transactions: int
    quarantined: int
    alerts_open: int
    alerts_escalated: int
    alerts_disposed: int
    cases: int
    latest_transaction_date: datetime | None
