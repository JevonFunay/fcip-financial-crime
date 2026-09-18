from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import UserRole
from app.schemas.ingestion import IngestionSummary
from app.services.ingestion import IngestionFileError, ingest_transactions_csv

router = APIRouter()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.post(
    "/transactions",
    response_model=IngestionSummary,
    dependencies=[Depends(require_role(UserRole.ROLE_DATA_OPS))],
)
def upload_transactions(file: UploadFile, db: Session = Depends(get_db)) -> IngestionSummary:
    raw_bytes = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds the 10 MB upload limit")

    try:
        content = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File must be UTF-8 encoded CSV") from None
    if "\x00" in content:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "File contains NUL bytes and is not a valid CSV")

    try:
        return ingest_transactions_csv(db, file_name=file.filename or "upload.csv", content=content)
    except IngestionFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
