import uuid
from datetime import date, datetime

from pydantic import BaseModel

from app.models.enums import IngestionBatchStatus


class QuarantinePreview(BaseModel):
    row_number: int
    error_reason: str


class IngestionSummary(BaseModel):
    file_name: str
    total_rows: int
    accepted: int
    quarantined: int
    # Null when ingestion ran outside a registered batch (seed script, unit
    # tests); uploads through the API always carry one (FR-101).
    batch_ref: str | None = None
    batch_status: str | None = None
    quarantine_preview: list[QuarantinePreview]


class IngestionBatchOut(BaseModel):
    id: uuid.UUID
    batch_ref: str
    source_system: str
    business_date: date
    file_name: str
    file_checksum: str
    file_size_bytes: int
    expected_records: int | None
    status: IngestionBatchStatus
    total_rows: int
    accepted_rows: int
    quarantined_rows: int
    correlation_id: uuid.UUID
    registered_by_email: str | None
    created_at: datetime
    completed_at: datetime | None


class ProcessingLogOut(BaseModel):
    event_code: str
    message: str
    details: dict | None
    created_at: datetime


class IngestionBatchDetail(IngestionBatchOut):
    processing_log: list[ProcessingLogOut]


class IngestionBatchListResponse(BaseModel):
    items: list[IngestionBatchOut]
    total: int
    limit: int
    offset: int
