import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.alert import Alert
from app.models.case import Case
from app.models.customer import Customer
from app.models.enums import AlertStatus, CaseStatus, UserRole
from app.models.user import User
from app.routers.alerts import _summary as alert_summary
from app.schemas.case import CaseCreateRequest, CaseDetail
from app.services.audit import OBJECT_CASE, record_transition

router = APIRouter()

ALL_ROLES = tuple(UserRole)


def _detail(db: Session, case_id: uuid.UUID) -> CaseDetail:
    row = db.execute(
        select(Case, User.email).join(User, Case.opened_by == User.id).where(Case.id == case_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    case, opened_by_email = row

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
        # A case's chain is the chain of the alert it was opened from.
        correlation_id=alert_rows[0][0].correlation_id,
        opened_by=case.opened_by,
        opened_by_email=opened_by_email,
        assigned_to=case.assigned_to,
        created_at=case.created_at,
        updated_at=case.updated_at,
        alerts=alerts,
    )


@router.post("", response_model=CaseDetail, status_code=status.HTTP_201_CREATED)
def create_case(
    payload: CaseCreateRequest,
    user: User = Depends(require_role(UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR)),
    db: Session = Depends(get_db),
) -> CaseDetail:
    alert = db.get(Alert, payload.alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    if alert.status is not AlertStatus.ESCALATED:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Alert must be ESCALATED to open a case (currently {alert.status.value})"
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


@router.get("/{case_id}", response_model=CaseDetail, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_case(case_id: uuid.UUID, db: Session = Depends(get_db)) -> CaseDetail:
    return _detail(db, case_id)
