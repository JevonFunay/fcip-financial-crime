import uuid
from datetime import datetime

from pydantic import BaseModel


class QuarantineOut(BaseModel):
    id: uuid.UUID
    source_file_name: str
    row_number: int
    raw_row: dict
    error_reason: str
    created_at: datetime


class QuarantineListResponse(BaseModel):
    items: list[QuarantineOut]
    total: int
    limit: int
    offset: int
