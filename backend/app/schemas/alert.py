import enum
import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel, field_validator

from app.models.enums import AlertStatus, RuleSeverity, RuleState, RuleWindowType
from app.schemas.transaction import TransactionOut

MIN_DISPOSITION_REASON_LENGTH = 20

__all__ = [
    "MIN_DISPOSITION_REASON_LENGTH",
    "AlertDetail",
    "AlertListResponse",
    "AlertRule",
    "AlertSummary",
    "DispositionDecision",
    "DispositionRequest",
    "TransactionOut",
]


class AlertSummary(BaseModel):
    id: uuid.UUID
    pattern_code: str
    status: AlertStatus
    customer_id: uuid.UUID
    customer_ref: str
    customer_name: str
    reason: str
    case_id: uuid.UUID | None
    created_at: datetime


class AlertRule(BaseModel):
    """The exact rule version that raised the alert (FR-302 flow 3), as it was
    then: a later version never changes what is shown here (FR-302 AC2)."""

    rule_id: uuid.UUID
    rule_ref: str
    version: int
    # The version's current state, e.g. RETIRED once FR-305 exists.
    state: RuleState
    template_version: str
    reason_code: str
    severity: RuleSeverity
    # Verbatim, exactly as written on the rule (FR-301 AC3, BR-301.2).
    description: str
    parameters: dict
    window_type: RuleWindowType
    window_length: timedelta


class AlertDetail(AlertSummary):
    correlation_id: uuid.UUID
    rule: AlertRule
    detection_details: dict
    disposed_by: uuid.UUID | None
    disposed_by_email: str | None
    disposed_at: datetime | None
    disposition_reason: str | None
    transactions: list[TransactionOut]


class AlertListResponse(BaseModel):
    items: list[AlertSummary]
    total: int
    limit: int
    offset: int


class DispositionDecision(str, enum.Enum):
    FALSE_POSITIVE = "false_positive"
    ESCALATE = "escalate"


class DispositionRequest(BaseModel):
    decision: DispositionDecision
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_must_be_substantive(cls, value: str) -> str:
        # FRD §5.0: a disposition without a real justification is rejected.
        cleaned = value.strip()
        if len(cleaned) < MIN_DISPOSITION_REASON_LENGTH:
            raise ValueError(f"reason must be at least {MIN_DISPOSITION_REASON_LENGTH} characters")
        return cleaned
