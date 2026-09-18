from pydantic import BaseModel


class QuarantinePreview(BaseModel):
    row_number: int
    error_reason: str


class IngestionSummary(BaseModel):
    file_name: str
    total_rows: int
    accepted: int
    quarantined: int
    quarantine_preview: list[QuarantinePreview]
