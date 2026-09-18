import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import CaseStatus
from app.schemas.alert import AlertSummary


class CaseCreateRequest(BaseModel):
    alert_id: uuid.UUID


class CaseDetail(BaseModel):
    id: uuid.UUID
    case_number: str
    status: CaseStatus
    correlation_id: uuid.UUID
    opened_by: uuid.UUID
    opened_by_email: str
    assigned_to: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    alerts: list[AlertSummary]
