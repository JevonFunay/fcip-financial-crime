import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.enums import CaseStatus
from app.schemas.alert import AlertSummary


class CaseCreateRequest(BaseModel):
    alert_id: uuid.UUID


class CaseTransitionRequest(BaseModel):
    """FR-804: move a case through its lifecycle."""
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_min_length(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("reason must be at least 10 characters")
        return v


class CaseAssignRequest(BaseModel):
    """Assign a case to an investigator (FR-603 extended to cases)."""
    assignee_id: uuid.UUID


class CaseSummary(BaseModel):
    id: uuid.UUID
    case_number: str
    status: CaseStatus
    opened_by_email: str
    assigned_to: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    alert_count: int


class CaseListResponse(BaseModel):
    items: list[CaseSummary]
    total: int
    limit: int
    offset: int


class CaseDetail(BaseModel):
    id: uuid.UUID
    case_number: str
    status: CaseStatus
    correlation_id: uuid.UUID
    opened_by: uuid.UUID
    opened_by_email: str
    assigned_to: uuid.UUID | None
    assigned_to_email: str | None
    closed_reason: str | None
    closure_submitted_by: uuid.UUID | None
    closure_approved_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    alerts: list[AlertSummary]
