import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class DetectedAlert(BaseModel):
    alert_id: uuid.UUID
    correlation_id: uuid.UUID
    customer_ref: str
    window_start: datetime
    window_end: datetime
    txn_count_in_band: int
    aggregate_amount: Decimal
    created: bool


class DetectionRunSummary(BaseModel):
    pattern_code: str
    detection_run_id: uuid.UUID
    alerts_created: int
    alerts_already_existing: int
    alerts: list[DetectedAlert]
