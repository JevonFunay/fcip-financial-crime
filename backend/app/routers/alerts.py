import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.core.rbac import require_role
from app.database import get_db
from app.models.account import Account
from app.models.alert import Alert, AlertTransaction
from app.models.customer import Customer
from app.models.enums import AlertStatus, UserRole
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.alert import (
    AlertDetail,
    AlertListResponse,
    AlertSummary,
    DispositionDecision,
    DispositionRequest,
    TransactionOut,
)
from app.services.audit import OBJECT_ALERT, record_transition

router = APIRouter()

ALL_ROLES = tuple(UserRole)
DECISION_TO_STATUS = {
    DispositionDecision.FALSE_POSITIVE: AlertStatus.DISPOSED,
    DispositionDecision.ESCALATE: AlertStatus.ESCALATED,
}


def _summary(alert: Alert, customer: Customer) -> AlertSummary:
    return AlertSummary(
        id=alert.id,
        pattern_code=alert.pattern_code,
        status=alert.status,
        customer_id=customer.id,
        customer_ref=customer.customer_ref,
        customer_name=customer.full_name,
        reason=alert.reason,
        case_id=alert.case_id,
        created_at=alert.created_at,
    )


def _evidence(db: Session, alert_id: uuid.UUID) -> list[TransactionOut]:
    rows = db.execute(
        select(Transaction, Account.account_number)
        .join(AlertTransaction, AlertTransaction.transaction_id == Transaction.id)
        .join(Account, Transaction.account_id == Account.id)
        .where(AlertTransaction.alert_id == alert_id)
        .order_by(Transaction.transaction_date, Transaction.transaction_ref)
    ).all()
    return [
        TransactionOut(
            id=t.id,
            transaction_ref=t.transaction_ref,
            account_number=account_number,
            transaction_date=t.transaction_date,
            amount=t.amount,
            currency=t.currency,
            direction=t.direction,
            channel=t.channel,
            counterparty_ref=t.counterparty_ref,
            description=t.description,
        )
        for t, account_number in rows
    ]


def _detail(db: Session, alert_id: uuid.UUID) -> AlertDetail:
    disposer = aliased(User)
    row = db.execute(
        select(Alert, Customer, disposer.email)
        .join(Customer, Alert.customer_id == Customer.id)
        .outerjoin(disposer, Alert.disposed_by == disposer.id)
        .where(Alert.id == alert_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    alert, customer, disposed_by_email = row
    return AlertDetail(
        **_summary(alert, customer).model_dump(),
        correlation_id=alert.correlation_id,
        detection_details=alert.detection_details,
        disposed_by=alert.disposed_by,
        disposed_by_email=disposed_by_email,
        disposed_at=alert.disposed_at,
        disposition_reason=alert.disposition_reason,
        transactions=_evidence(db, alert.id),
    )


@router.get("", response_model=AlertListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def list_alerts(
    status_filter: AlertStatus | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> AlertListResponse:
    conditions = [Alert.status == status_filter] if status_filter is not None else []
    total = db.scalar(select(func.count()).select_from(Alert).where(*conditions)) or 0
    rows = db.execute(
        select(Alert, Customer)
        .join(Customer, Alert.customer_id == Customer.id)
        .where(*conditions)
        .order_by(Alert.created_at.desc(), Alert.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return AlertListResponse(
        items=[_summary(alert, customer) for alert, customer in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{alert_id}", response_model=AlertDetail, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_alert(alert_id: uuid.UUID, db: Session = Depends(get_db)) -> AlertDetail:
    return _detail(db, alert_id)


@router.post("/{alert_id}/disposition", response_model=AlertDetail)
def dispose_alert(
    alert_id: uuid.UUID,
    payload: DispositionRequest,
    user: User = Depends(require_role(UserRole.ROLE_TRIAGE)),
    db: Session = Depends(get_db),
) -> AlertDetail:
    alert = db.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Alert not found")
    if alert.status is not AlertStatus.OPEN:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Alert is already {alert.status.value}")

    new_status = DECISION_TO_STATUS[payload.decision]
    alert.status = new_status
    alert.disposed_by = user.id
    alert.disposed_at = datetime.now(timezone.utc)
    alert.disposition_reason = payload.reason
    record_transition(
        db,
        correlation_id=alert.correlation_id,
        actor=user,
        object_type=OBJECT_ALERT,
        object_id=alert.id,
        from_state=AlertStatus.OPEN.value,
        to_state=new_status.value,
        reason=payload.reason,
    )
    db.commit()
    return _detail(db, alert.id)
