import hashlib
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.enums import IngestionBatchStatus, UserRole
from app.models.ingestion_batch import IngestionBatch
from app.models.processing_log import EVENT_BATCH_FAILED, EVENT_BATCH_REGISTERED, ProcessingLog
from app.models.quarantine_item import QuarantineItem
from app.models.user import User
from app.schemas.ingestion import (
    IngestionBatchDetail,
    IngestionBatchListResponse,
    IngestionBatchOut,
    IngestionSummary,
    ProcessingLogOut,
)
from app.schemas.quarantine import QuarantineListResponse, QuarantineOut
from app.services.audit import OBJECT_BATCH, record_transition
from app.services.ingestion import IngestionFileError, ingest_transactions_csv

router = APIRouter()

ALL_ROLES = tuple(UserRole)

# FRD §14.1 NFR-05 requires ingesting 100k transactions; a realistic 100k-row
# CSV runs ~10-12 MB, so the cap needs headroom above that, not just above a
# "typical" file.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

# FR-101 wants a named source system. A browser upload has no upstream system
# to name, so it is labelled explicitly rather than left blank.
DEFAULT_SOURCE_SYSTEM = "MANUAL_UPLOAD"


def _batch_out(batch: IngestionBatch, registered_by_email: str | None) -> IngestionBatchOut:
    return IngestionBatchOut(
        id=batch.id,
        batch_ref=batch.batch_ref,
        source_system=batch.source_system,
        business_date=batch.business_date,
        file_name=batch.file_name,
        file_checksum=batch.file_checksum,
        file_size_bytes=batch.file_size_bytes,
        expected_records=batch.expected_records,
        status=batch.status,
        total_rows=batch.total_rows,
        accepted_rows=batch.accepted_rows,
        quarantined_rows=batch.quarantined_rows,
        correlation_id=batch.correlation_id,
        registered_by_email=registered_by_email,
        created_at=batch.created_at,
        completed_at=batch.completed_at,
    )


def _register_batch(
    db: Session,
    *,
    actor: User,
    file_name: str,
    raw_bytes: bytes,
    source_system: str,
    business_date: date,
    expected_records: int | None,
) -> IngestionBatch:
    """FR-101: publish a batch id, store the file checksum, and reject a file
    already registered for the same source system and business date."""
    checksum = hashlib.sha256(raw_bytes).hexdigest()

    existing = db.scalar(
        select(IngestionBatch).where(
            IngestionBatch.source_system == source_system,
            IngestionBatch.business_date == business_date,
            IngestionBatch.file_checksum == checksum,
        )
    )
    if existing is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"This file was already registered as {existing.batch_ref} for {source_system} "
            f"on business date {business_date.isoformat()}",
        )

    sequence_value = db.scalar(select(func.nextval("batch_ref_seq")))
    batch = IngestionBatch(
        batch_ref=f"BAT-{sequence_value:06d}",
        source_system=source_system,
        business_date=business_date,
        file_name=file_name,
        file_checksum=checksum,
        file_size_bytes=len(raw_bytes),
        expected_records=expected_records,
        status=IngestionBatchStatus.REGISTERED,
        correlation_id=uuid.uuid4(),
        registered_by=actor.id,
    )
    db.add(batch)
    db.flush()
    db.add(
        ProcessingLog(
            ingestion_batch_id=batch.id,
            event_code=EVENT_BATCH_REGISTERED,
            message=f"Registered {file_name} ({len(raw_bytes)} bytes) for {source_system}",
            details={"file_checksum": checksum, "expected_records": expected_records},
        )
    )
    record_transition(
        db,
        correlation_id=batch.correlation_id,
        actor=actor,
        object_type=OBJECT_BATCH,
        object_id=batch.id,
        from_state=None,
        to_state=IngestionBatchStatus.REGISTERED.value,
        reason=f"Registered {file_name} for {source_system} on {business_date.isoformat()}",
    )
    # Committed before parsing starts: a batch that then fails structurally has
    # to stay visible as FAILED (TRD C-01), which it cannot do if the row is
    # rolled back along with the failure.
    db.commit()
    return batch


def _fail_batch(db: Session, batch_id: uuid.UUID, *, actor: User, reason: str) -> None:
    """TRD C-01: a structural failure leaves the batch FAILED with zero rows loaded."""
    db.rollback()
    batch = db.get(IngestionBatch, batch_id)
    if batch is None:  # pragma: no cover - the batch was committed just above
        return
    batch.status = IngestionBatchStatus.FAILED
    batch.completed_at = datetime.now(timezone.utc)
    db.add(
        ProcessingLog(
            ingestion_batch_id=batch.id,
            event_code=EVENT_BATCH_FAILED,
            message=reason,
        )
    )
    record_transition(
        db,
        correlation_id=batch.correlation_id,
        actor=actor,
        object_type=OBJECT_BATCH,
        object_id=batch.id,
        from_state=IngestionBatchStatus.REGISTERED.value,
        to_state=IngestionBatchStatus.FAILED.value,
        reason=reason,
    )
    db.commit()


@router.post("/transactions", response_model=IngestionSummary)
def upload_transactions(
    file: UploadFile,
    source_system: str = Form(DEFAULT_SOURCE_SYSTEM),
    business_date: date | None = Form(None),
    expected_records: int | None = Form(None),
    user: User = Depends(require_role(UserRole.ROLE_DATA_OPS)),
    db: Session = Depends(get_db),
) -> IngestionSummary:
    raw_bytes = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw_bytes) > MAX_UPLOAD_BYTES:
        limit_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"File exceeds the {limit_mb} MB upload limit")

    file_name = file.filename or "upload.csv"
    batch = _register_batch(
        db,
        actor=user,
        file_name=file_name,
        raw_bytes=raw_bytes,
        source_system=source_system,
        # Defaults to the day the upload happens; a backdated file should pass
        # its own business date so the batch is filed against the right day.
        business_date=business_date or datetime.now(timezone.utc).date(),
        expected_records=expected_records,
    )

    try:
        content = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        reason = "File must be UTF-8 encoded CSV"
        _fail_batch(db, batch.id, actor=user, reason=reason)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason) from None
    if "\x00" in content:
        reason = "File contains NUL bytes and is not a valid CSV"
        _fail_batch(db, batch.id, actor=user, reason=reason)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, reason)

    try:
        return ingest_transactions_csv(db, file_name=file_name, content=content, batch=batch, actor=user)
    except IngestionFileError as exc:
        _fail_batch(db, batch.id, actor=user, reason=str(exc))
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.get("/batches", response_model=IngestionBatchListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def list_batches(
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> IngestionBatchListResponse:
    total = db.scalar(select(func.count()).select_from(IngestionBatch)) or 0
    rows = db.execute(
        select(IngestionBatch, User.email)
        .outerjoin(User, IngestionBatch.registered_by == User.id)
        .order_by(IngestionBatch.created_at.desc())
        .limit(limit)
        .offset(offset)
    ).all()

    return IngestionBatchListResponse(
        items=[_batch_out(batch, email) for batch, email in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/batches/{batch_id}", response_model=IngestionBatchDetail, dependencies=[Depends(require_role(*ALL_ROLES))]
)
def get_batch(batch_id: uuid.UUID, db: Session = Depends(get_db)) -> IngestionBatchDetail:
    row = db.execute(
        select(IngestionBatch, User.email)
        .outerjoin(User, IngestionBatch.registered_by == User.id)
        .where(IngestionBatch.id == batch_id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ingestion batch not found")
    batch, email = row

    entries = db.execute(
        select(ProcessingLog)
        .where(ProcessingLog.ingestion_batch_id == batch_id)
        .order_by(ProcessingLog.seq)
    ).scalars()

    return IngestionBatchDetail(
        **_batch_out(batch, email).model_dump(),
        processing_log=[ProcessingLogOut.model_validate(entry, from_attributes=True) for entry in entries],
    )


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
