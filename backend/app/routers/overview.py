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

    return OverviewSummary(
        customers=count(Customer),
        accounts=count(Account),
        transactions=count(Transaction),
        quarantined=count(QuarantineItem),
        alerts_open=count(Alert, Alert.status == AlertStatus.OPEN),
        alerts_escalated=count(Alert, Alert.status == AlertStatus.ESCALATED),
        alerts_disposed=count(Alert, Alert.status == AlertStatus.DISPOSED),
        cases=count(Case),
        latest_transaction_date=db.scalar(select(func.max(Transaction.transaction_date))),
    )
