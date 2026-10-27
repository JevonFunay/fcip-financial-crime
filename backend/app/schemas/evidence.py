"""Pydantic schemas for evidence (FR-708, FR-811–813)."""
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enums import EvidenceType


class EvidenceOut(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    uploaded_by: uuid.UUID
    uploaded_by_email: str
    evidence_type: EvidenceType
    file_name: str
    file_hash: str | None
    file_size_bytes: int | None
    storage_uri: str | None
    description: str | None
    created_at: datetime


class EvidenceListResponse(BaseModel):
    items: list[EvidenceOut]
    total: int
