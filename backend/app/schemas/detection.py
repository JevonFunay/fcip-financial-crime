import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class EvaluatedRule(BaseModel):
    rule_ref: str
    version: int


class DetectedAlert(BaseModel):
    alert_id: uuid.UUID
    correlation_id: uuid.UUID
    customer_ref: str
    # The rule version that raised it (FR-302 flow 3).
    rule_ref: str
    rule_version: int
    window_start: datetime
    window_end: datetime
    txn_count_in_band: int
    aggregate_amount: Decimal
    created: bool


class DetectionRunSummary(BaseModel):
    pattern_code: str
    detection_run_id: uuid.UUID
    # Every ACTIVE rule version the run evaluated; empty when the pattern has
    # no live rule, so "nothing to evaluate" is visible, not silent.
    rules_evaluated: list[EvaluatedRule]
    alerts_created: int
    alerts_already_existing: int
    alerts: list[DetectedAlert]
