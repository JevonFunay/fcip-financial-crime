"""Evidence router (FR-708, FR-811–813).

Evidence is immutable once uploaded (DB trigger enforces this).
File storage is simplified: content is stored server-side as a URI reference.
MinIO / content-addressed storage (FR-811 full spec) deferred — recorded as
a simplification in PROJECT_CONTEXT.md §7.
"""
import hashlib
import io
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.case import Case
from app.models.enums import CaseStatus, EvidenceType, UserRole
from app.models.evidence import Evidence
from app.models.user import User
from app.schemas.evidence import EvidenceListResponse, EvidenceOut
from app.services.audit import OBJECT_CASE, record_transition
from app.routers.cases import _first_alert_correlation

router = APIRouter()

# Allowed roles: investigators and above can upload; all roles can read.
UPLOAD_ROLES = (
    UserRole.ROLE_INVESTIGATOR,
    UserRole.ROLE_SENIOR_INVESTIGATOR,
    UserRole.ROLE_MLRO,
    UserRole.ROLE_TRIAGE,
    UserRole.ROLE_ADMIN,
)
ALL_ROLES = tuple(UserRole)

# Simplified local storage: evidence files are stored in-memory as data URIs
# (base64) for demo purposes. In production this would be MinIO (FR-811).
# Max 10 MB per file for evidence.
MAX_EVIDENCE_BYTES = 10 * 1024 * 1024


def _evidence_out(ev: Evidence, uploader_email: str) -> EvidenceOut:
    return EvidenceOut(
        id=ev.id,
        case_id=ev.case_id,
        uploaded_by=ev.uploaded_by,
        uploaded_by_email=uploader_email,
        evidence_type=ev.evidence_type,
        file_name=ev.file_name,
        file_hash=ev.file_hash,
        file_size_bytes=ev.file_size_bytes,
        storage_uri=ev.storage_uri,
        description=ev.description,
        created_at=ev.created_at,
    )


@router.post(
    "/{case_id}/evidence",
    response_model=EvidenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_evidence(
    case_id: uuid.UUID,
    file: UploadFile,
    evidence_type: EvidenceType = Query(EvidenceType.DOCUMENT),
    description: str | None = Query(None),
    user: User = Depends(require_role(*UPLOAD_ROLES)),
    db: Session = Depends(get_db),
) -> EvidenceOut:
    """FR-811: attach a file or note to a case.  The file hash is SHA-256 (FR-813)."""
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    if case.status is CaseStatus.CLOSED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Cannot add evidence to a CLOSED case")

    content = await file.read()
    if len(content) > MAX_EVIDENCE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 10 MB limit")

    file_hash = hashlib.sha256(content).hexdigest()
    # Simplified storage: record the hash; in full implementation store to MinIO.
    storage_uri = f"evidence://{case_id}/{file_hash}"

    correlation_id = _first_alert_correlation(db, case_id)
    ev = Evidence(
        case_id=case_id,
        uploaded_by=user.id,
        correlation_id=correlation_id,
        evidence_type=evidence_type,
        file_name=file.filename or "upload",
        file_hash=file_hash,
        file_size_bytes=len(content),
        storage_uri=storage_uri,
        description=description,
    )
    db.add(ev)
    db.flush()
    record_transition(
        db,
        correlation_id=correlation_id,
        actor=user,
        object_type=OBJECT_CASE,
        object_id=case.id,
        from_state=case.status.value,
        to_state=case.status.value,
        action="EVIDENCE_UPLOADED",
        reason=f"Evidence attached: {file.filename}",
        details={
            "evidence_id": str(ev.id),
            "file_name": file.filename,
            "file_hash": file_hash,
            "evidence_type": evidence_type.value,
        },
    )
    db.commit()
    return _evidence_out(ev, user.email)


@router.get(
    "/{case_id}/evidence",
    response_model=EvidenceListResponse,
    dependencies=[Depends(require_role(*ALL_ROLES))],
)
def list_evidence(
    case_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> EvidenceListResponse:
    """List all evidence items for a case."""
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")

    rows = db.execute(
        select(Evidence, User.email)
        .join(User, Evidence.uploaded_by == User.id)
        .where(Evidence.case_id == case_id)
        .order_by(Evidence.created_at)
    ).all()
    items = [_evidence_out(ev, email) for ev, email in rows]
    return EvidenceListResponse(items=items, total=len(items))
