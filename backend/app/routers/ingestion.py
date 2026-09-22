from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import UserRole
from app.models.quarantine_item import QuarantineItem
from app.schemas.ingestion import IngestionSummary
from app.schemas.quarantine import QuarantineListResponse, QuarantineOut
from app.services.ingestion import IngestionFileError, ingest_transactions_csv

router = APIRouter()

ALL_ROLES = tuple(UserRole)

# FRD §14.1 NFR-05 requires ingesting 100k transactions; a realistic 100k-row
# CSV runs ~10-12 MB, so the cap needs headroom above that, not just above a
# "typical" file.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@router.post(
    "/transactions",
    response_model=IngestionSummary,
    dependencies=[Depends(require_role(UserRole.ROLE_DATA_OPS))],
)
def upload_transactions(file: UploadFile, db: Session = Depends(get_db)) -> IngestionSummary:
    raw_bytes = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds the {limit_mb} MB upload limit")

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


@router.get("/quarantine", response_model=QuarantineListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def list_quarantine(
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> QuarantineListResponse:
    total = db.scalar(select(func.count()).select_from(QuarantineItem)) or 0
    items = db.execute(
        select(QuarantineItem)
        .order_by(QuarantineItem.created_at.desc(), QuarantineItem.row_number)
        .limit(limit)
        .offset(offset)
    ).scalars()

    return QuarantineListResponse(
        items=[QuarantineOut.model_validate(item, from_attributes=True) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )
