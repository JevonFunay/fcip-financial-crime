"""Case management router.

FR-801: create case from escalated alert.
FR-803: get case detail.
FR-804: case lifecycle — start investigation, submit closure, approve/reject closure (maker-checker).
FR-808: case queue (filterable, paged).
FR-603 ext: assign case to an investigator.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.rbac import require_role
from app.database import get_db
from app.models.alert import Alert
from app.models.case import Case
from app.models.customer import Customer
from app.models.enums import AlertStatus, CaseStatus, UserRole
from app.models.user import User
from app.routers.alerts import _summary as alert_summary
from app.schemas.case import (
    CaseAssignRequest,
    CaseCreateRequest,
    CaseDetail,
    CaseListResponse,
    CaseSummary,
    CaseTransitionRequest,
)
from app.services.audit import OBJECT_CASE, record_transition

router = APIRouter()

ALL_ROLES = tuple(UserRole)

# ── helpers ────────────────────────────────────────────────────────────────────

_opener = aliased(User, name="opener")
_assignee = aliased(User, name="assignee")


def _detail(db: Session, case_id: uuid.UUID) -> CaseDetail:
    row = db.execute(
        select(Case, _opener.email, _assignee.email)
        .join(_opener, Case.opened_by == _opener.id)
        .outerjoin(_assignee, Case.assigned_to == _assignee.id)
        .where(Case.id == case_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    case, opened_by_email, assigned_to_email = row

    alert_rows = db.execute(
        select(Alert, Customer)
        .join(Customer, Alert.customer_id == Customer.id)
        .where(Alert.case_id == case.id)
        .order_by(Alert.created_at, Alert.id)
    ).all()
    alerts = [alert_summary(alert, customer) for alert, customer in alert_rows]
    return CaseDetail(
        id=case.id,
        case_number=case.case_number,
        status=case.status,
        correlation_id=alert_rows[0][0].correlation_id if alert_rows else uuid.uuid4(),
        opened_by=case.opened_by,
        opened_by_email=opened_by_email,
        assigned_to=case.assigned_to,
        assigned_to_email=assigned_to_email,
        closed_reason=case.closed_reason,
        closure_submitted_by=case.closure_submitted_by,
        closure_approved_by=case.closure_approved_by,
        created_at=case.created_at,
        updated_at=case.updated_at,
        alerts=alerts,
    )


def _get_case_or_404(db: Session, case_id: uuid.UUID) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    return case


# ── POST /cases ────────────────────────────────────────────────────────────────

@router.post("", response_model=CaseDetail, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreateRequest,
    user: User = Depends(require_role(UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR)),
    db: Session = Depends(get_db),
) -> CaseDetail:
    """FR-801: create a case from an escalated alert."""
    alert = db.get(Alert, payload.alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    if alert.status is not AlertStatus.ESCALATED:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Alert must be ESCALATED to open a case (currently {alert.status.value})",
        )
    if alert.case_id is not None:
        existing = db.get(Case, alert.case_id)
        raise HTTPException(status.HTTP_409_CONFLICT, f"Alert is already linked to case {existing.case_number}")

    sequence_value = db.scalar(select(func.nextval("case_number_seq")))
    case = Case(case_number=f"CASE-{sequence_value:06d}", status=CaseStatus.OPEN, opened_by=user.id)
    db.add(case)
    db.flush()
    alert.case_id = case.id
    record_transition(
        db,
        correlation_id=alert.correlation_id,
        actor=user,
        object_type=OBJECT_CASE,
        object_id=case.id,
        from_state=None,
        to_state=CaseStatus.OPEN.value,
        reason=f"Opened from escalated alert {alert.id}",
    )
    db.commit()
    return _detail(db, case.id)


# ── GET /cases ─────────────────────────────────────────────────────────────────

@router.get("", response_model=CaseListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def list_cases(
    status_filter: CaseStatus | None = Query(None, alias="status"),
    assigned_to: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> CaseListResponse:
    """FR-808: paged, filterable case queue."""
    conditions = []
    if status_filter is not None:
        conditions.append(Case.status == status_filter)
    if assigned_to is not None:
        conditions.append(Case.assigned_to == assigned_to)

    total = db.scalar(select(func.count()).select_from(Case).where(*conditions)) or 0
    rows = db.execute(
        select(Case, _opener.email, func.count(Alert.id).label("alert_count"))
        .join(_opener, Case.opened_by == _opener.id)
        .outerjoin(Alert, Alert.case_id == Case.id)
        .where(*conditions)
        .group_by(Case.id, _opener.email)
        .order_by(Case.created_at.desc(), Case.id)
        .limit(limit)
        .offset(offset)
    ).all()
    items = [
        CaseSummary(
            id=case.id,
            case_number=case.case_number,
            status=case.status,
            opened_by_email=email,
            assigned_to=case.assigned_to,
            created_at=case.created_at,
            updated_at=case.updated_at,
            alert_count=alert_count,
        )
        for case, email, alert_count in rows
    ]
    return CaseListResponse(items=items, total=total, limit=limit, offset=offset)


# ── GET /cases/{id} ────────────────────────────────────────────────────────────

@router.get("/{case_id}", response_model=CaseDetail, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_case(case_id: uuid.UUID, db: Session = Depends(get_db)) -> CaseDetail:
    return _detail(db, case_id)


# ── POST /cases/{id}/start ─────────────────────────────────────────────────────

@router.post("/{case_id}/start", response_model=CaseDetail)
def start_investigation(
    case_id: uuid.UUID,
    user: User = Depends(
        require_role(
            UserRole.ROLE_INVESTIGATOR,
            UserRole.ROLE_SENIOR_INVESTIGATOR,
            UserRole.ROLE_MLRO,
        )
    ),
    db: Session = Depends(get_db),
) -> CaseDetail:
    """FR-804: move case from OPEN → IN_PROGRESS."""
    case = _get_case_or_404(db, case_id)
    if case.status is not CaseStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Case is {case.status.value}, expected OPEN")
    from_state = case.status
    case.status = CaseStatus.IN_PROGRESS
    record_transition(
        db,
        correlation_id=_first_alert_correlation(db, case_id),
        actor=user,
        object_type=OBJECT_CASE,
        object_id=case.id,
        from_state=from_state.value,
        to_state=CaseStatus.IN_PROGRESS.value,
        action="CASE_INVESTIGATION_STARTED",
    )
    db.commit()
    return _detail(db, case_id)


# ── POST /cases/{id}/submit-closure ───────────────────────────────────────────

@router.post("/{case_id}/submit-closure", response_model=CaseDetail)
def submit_closure(
    case_id: uuid.UUID,
    payload: CaseTransitionRequest,
    user: User = Depends(
        require_role(
            UserRole.ROLE_INVESTIGATOR,
            UserRole.ROLE_SENIOR_INVESTIGATOR,
        )
    ),
    db: Session = Depends(get_db),
) -> CaseDetail:
    """FR-814 maker step: investigator submits closure; moves to PENDING_CLOSURE."""
    case = _get_case_or_404(db, case_id)
    if case.status not in (CaseStatus.OPEN, CaseStatus.IN_PROGRESS):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Cannot submit closure from state {case.status.value}",
        )
    from_state = case.status
    case.status = CaseStatus.PENDING_CLOSURE
    case.closed_reason = payload.reason
    case.closure_submitted_by = user.id
    record_transition(
        db,
        correlation_id=_first_alert_correlation(db, case_id),
        actor=user,
        object_type=OBJECT_CASE,
        object_id=case.id,
        from_state=from_state.value,
        to_state=CaseStatus.PENDING_CLOSURE.value,
        action="CASE_CLOSURE_SUBMITTED",
        reason=payload.reason,
    )
    db.commit()
    return _detail(db, case_id)


# ── POST /cases/{id}/approve-closure ──────────────────────────────────────────

@router.post("/{case_id}/approve-closure", response_model=CaseDetail)
def approve_closure(
    case_id: uuid.UUID,
    user: User = Depends(
        require_role(
            UserRole.ROLE_SENIOR_INVESTIGATOR,
            UserRole.ROLE_MLRO,
        )
    ),
    db: Session = Depends(get_db),
) -> CaseDetail:
    """FR-814–816 checker step: MLRO/Senior approves closure → CLOSED."""
    case = _get_case_or_404(db, case_id)
    if case.status is not CaseStatus.PENDING_CLOSURE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Case is {case.status.value}, expected PENDING_CLOSURE",
        )
    # Four-eyes: checker must differ from maker (FR-815 SoD)
    if case.closure_submitted_by == user.id:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Checker cannot be the same person as the maker (four-eyes principle)",
        )
    case.status = CaseStatus.CLOSED
    case.closure_approved_by = user.id
    record_transition(
        db,
        correlation_id=_first_alert_correlation(db, case_id),
        actor=user,
        object_type=OBJECT_CASE,
        object_id=case.id,
        from_state=CaseStatus.PENDING_CLOSURE.value,
        to_state=CaseStatus.CLOSED.value,
        action="CASE_CLOSURE_APPROVED",
        reason=case.closed_reason,
    )
    db.commit()
    return _detail(db, case_id)


# ── POST /cases/{id}/reject-closure ──────────────────────────────────────────

@router.post("/{case_id}/reject-closure", response_model=CaseDetail)
def reject_closure(
    case_id: uuid.UUID,
    payload: CaseTransitionRequest,
    user: User = Depends(
        require_role(
            UserRole.ROLE_SENIOR_INVESTIGATOR,
            UserRole.ROLE_MLRO,
        )
    ),
    db: Session = Depends(get_db),
) -> CaseDetail:
    """FR-816: checker rejects closure → back to IN_PROGRESS."""
    case = _get_case_or_404(db, case_id)
    if case.status is not CaseStatus.PENDING_CLOSURE:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Case is {case.status.value}, expected PENDING_CLOSURE",
        )
    case.status = CaseStatus.IN_PROGRESS
    case.closed_reason = None
    case.closure_submitted_by = None
    record_transition(
        db,
        correlation_id=_first_alert_correlation(db, case_id),
        actor=user,
        object_type=OBJECT_CASE,
        object_id=case.id,
        from_state=CaseStatus.PENDING_CLOSURE.value,
        to_state=CaseStatus.IN_PROGRESS.value,
        action="CASE_CLOSURE_REJECTED",
        reason=payload.reason,
    )
    db.commit()
    return _detail(db, case_id)


# ── POST /cases/{id}/assign ────────────────────────────────────────────────────

@router.post("/{case_id}/assign", response_model=CaseDetail)
def assign_case(
    case_id: uuid.UUID,
    payload: CaseAssignRequest,
    user: User = Depends(
        require_role(
            UserRole.ROLE_TRIAGE,
            UserRole.ROLE_SENIOR_INVESTIGATOR,
            UserRole.ROLE_MLRO,
            UserRole.ROLE_ADMIN,
        )
    ),
    db: Session = Depends(get_db),
) -> CaseDetail:
    """Assign a case to an investigator."""
    case = _get_case_or_404(db, case_id)
    assignee = db.get(User, payload.assignee_id)
    if assignee is None or not assignee.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignee not found")
    allowed = {UserRole.ROLE_INVESTIGATOR, UserRole.ROLE_SENIOR_INVESTIGATOR, UserRole.ROLE_ANALYST}
    if assignee.role not in allowed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Cannot assign to role {assignee.role.value}",
        )
    case.assigned_to = assignee.id
    record_transition(
        db,
        correlation_id=_first_alert_correlation(db, case_id),
        actor=user,
        object_type=OBJECT_CASE,
        object_id=case.id,
        from_state=case.status.value,
        to_state=case.status.value,
        action="CASE_ASSIGNED",
        reason=f"Assigned to {assignee.email}",
        details={"assignee_id": str(assignee.id), "assignee_email": assignee.email},
    )
    db.commit()
    return _detail(db, case_id)


# ── helper ─────────────────────────────────────────────────────────────────────

def _first_alert_correlation(db: Session, case_id: uuid.UUID) -> uuid.UUID:
    """Return the correlation_id of the first alert in the case, for audit chaining."""
    alert = db.scalar(
        select(Alert).where(Alert.case_id == case_id).order_by(Alert.created_at, Alert.id).limit(1)
    )
    if alert is None:
        return uuid.uuid4()
    return alert.correlation_id
