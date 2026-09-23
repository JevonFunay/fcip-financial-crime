import uuid
from datetime import datetime

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: uuid.UUID
    correlation_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    actor_role: str | None
    object_type: str
    object_id: uuid.UUID
    from_state: str | None
    to_state: str
    reason: str | None
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditEventOut]
    total: int
    limit: int
    offset: int
