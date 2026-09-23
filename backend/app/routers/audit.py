import csv
import io
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.audit_log import AuditLog
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.audit import AuditEventOut, AuditListResponse
from app.services.audit import OBJECT_AUDIT_EXPORT, record_transition

router = APIRouter()

# FRD §4.1.10 gives ROLE_AUDITOR read access to the whole audit trail. That
# role does not exist yet (only 4 of the 9 FRD roles are built), so for now
# every authenticated role can read it — narrowing this is part of the
# outstanding RBAC work, not a decision taken here.
ALL_ROLES = tuple(UserRole)

EXPORT_LIMIT = 10_000
EXPORT_COLUMNS = (
    "created_at",
    "correlation_id",
    "object_type",
    "object_id",
    "from_state",
    "to_state",
    "actor_email",
    "actor_role",
    "reason",
)


def _filtered(
    statement: Select,
    *,
    correlation_id: uuid.UUID | None,
    object_type: str | None,
    object_id: uuid.UUID | None,
    actor_email: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Select:
    if correlation_id is not None:
        statement = statement.where(AuditLog.correlation_id == correlation_id)
    if object_type is not None:
        statement = statement.where(AuditLog.object_type == object_type.upper())
    if object_id is not None:
        statement = statement.where(AuditLog.object_id == object_id)
    if actor_email is not None:
        statement = statement.where(func.lower(User.email) == actor_email.lower())
    if date_from is not None:
        statement = statement.where(AuditLog.created_at >= date_from)
    if date_to is not None:
        statement = statement.where(AuditLog.created_at <= date_to)
    return statement


def _rows(
    db: Session,
    *,
    correlation_id: uuid.UUID | None,
    object_type: str | None,
    object_id: uuid.UUID | None,
    actor_email: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[AuditEventOut], int]:
    filters = {
        "correlation_id": correlation_id,
        "object_type": object_type,
        "object_id": object_id,
        "actor_email": actor_email,
        "date_from": date_from,
        "date_to": date_to,
    }
    base = select(AuditLog, User.email).outerjoin(User, AuditLog.actor_user_id == User.id)
    counted = select(func.count()).select_from(AuditLog).outerjoin(User, AuditLog.actor_user_id == User.id)

    total = db.scalar(_filtered(counted, **filters)) or 0
    result = db.execute(
        # Ascending, so a correlation_id search reads as the chain in the order
        # it happened (NFR-13) rather than newest-first.
        _filtered(base, **filters).order_by(AuditLog.created_at, AuditLog.id).limit(limit).offset(offset)
    ).all()

    items = [
        AuditEventOut(
            id=entry.id,
            correlation_id=entry.correlation_id,
            actor_user_id=entry.actor_user_id,
            actor_email=email,
            actor_role=entry.actor_role,
            object_type=entry.object_type,
            object_id=entry.object_id,
            from_state=entry.from_state,
            to_state=entry.to_state,
            reason=entry.reason,
            created_at=entry.created_at,
        )
        for entry, email in result
    ]
    return items, total


@router.get("", response_model=AuditListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def search_audit(
    correlation_id: uuid.UUID | None = Query(None),
    object_type: str | None = Query(None, description="ALERT, CASE, BATCH, DETECTION_RUN, AUDIT_EXPORT"),
    object_id: uuid.UUID | None = Query(None),
    actor_email: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> AuditListResponse:
    """FR-1105: search the audit trail. Searching by `correlation_id` returns a
    whole chain (e.g. alert raised -> escalated -> case opened) in order."""
    items, total = _rows(
        db,
        correlation_id=correlation_id,
        object_type=object_type,
        object_id=object_id,
        actor_email=actor_email,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return AuditListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/export")
def export_audit(
    correlation_id: uuid.UUID | None = Query(None),
    object_type: str | None = Query(None),
    object_id: uuid.UUID | None = Query(None),
    actor_email: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    user: User = Depends(require_role(*ALL_ROLES)),
    db: Session = Depends(get_db),
) -> Response:
    """FR-1105 export. The export is itself an audited action (FRD §4.1.10),
    recorded with the filters that produced it."""
    items, total = _rows(
        db,
        correlation_id=correlation_id,
        object_type=object_type,
        object_id=object_id,
        actor_email=actor_email,
        date_from=date_from,
        date_to=date_to,
        limit=EXPORT_LIMIT,
        offset=0,
    )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        row = item.model_dump()
        writer.writerow({column: ("" if row[column] is None else str(row[column])) for column in EXPORT_COLUMNS})

    applied = {
        name: str(value)
        for name, value in (
            ("correlation_id", correlation_id),
            ("object_type", object_type),
            ("object_id", object_id),
            ("actor_email", actor_email),
            ("date_from", date_from),
            ("date_to", date_to),
        )
        if value is not None
    }
    record_transition(
        db,
        correlation_id=uuid.uuid4(),
        actor=user,
        object_type=OBJECT_AUDIT_EXPORT,
        object_id=uuid.uuid4(),
        from_state=None,
        to_state="EXPORTED",
        reason=(
            f"Exported {len(items)} of {total} audit event(s); "
            f"filters: {applied or 'none'}"
            + (f"; truncated at {EXPORT_LIMIT}" if total > len(items) else "")
        ),
    )
    db.commit()

    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="audit_trail.csv"'},
    )
