from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.rbac import require_role
from app.database import get_db
from app.models.account import Account
from app.models.alert import Alert
from app.models.case import Case
from app.models.customer import Customer
from app.models.enums import AlertStatus, UserRole
from app.models.quarantine_item import QuarantineItem
from app.models.transaction import Transaction
from app.schemas.overview import OverviewSummary

router = APIRouter()

ALL_ROLES = tuple(UserRole)


@router.get("", response_model=OverviewSummary, dependencies=[Depends(require_role(*ALL_ROLES))])
def get_overview(db: Session = Depends(get_db)) -> OverviewSummary:
    def count(model, *conditions) -> int:
        return db.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    # Keep precision and timezone knowledge attached to the same latest row,
    # including when several observations share a timestamp carrier.
    latest = db.execute(select(Transaction.transaction_date, Transaction.timestamp_precision,
                               Transaction.source_timezone_known)
                        .order_by(Transaction.transaction_date.desc(), Transaction.transaction_ref)
                        .limit(1)).first()
    return OverviewSummary(
        customers=count(Customer),
        accounts=count(Account),
        transactions=count(Transaction),
        quarantined=count(QuarantineItem),
        alerts_open=count(Alert, Alert.status == AlertStatus.OPEN),
        alerts_escalated=count(Alert, Alert.status == AlertStatus.ESCALATED),
        alerts_disposed=count(Alert, Alert.status == AlertStatus.DISPOSED),
        cases=count(Case),
        latest_transaction_date=latest.transaction_date if latest else None,
        latest_transaction_timestamp_precision=latest.timestamp_precision if latest else None,
        latest_transaction_source_timezone_known=latest.source_timezone_known if latest else None,
    )
