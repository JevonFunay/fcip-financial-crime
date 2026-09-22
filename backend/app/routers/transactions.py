from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from app.core.rbac import require_role
from app.database import get_db
from app.models.account import Account
from app.models.customer import Customer
from app.models.enums import TransactionDirection, UserRole
from app.models.transaction import Transaction
from app.schemas.transaction import TransactionListResponse, TransactionRow

router = APIRouter()

ALL_ROLES = tuple(UserRole)


def _with_owner(statement: Select) -> Select:
    return statement.join(Account, Transaction.account_id == Account.id).join(
        Customer, Account.customer_id == Customer.id
    )


@router.get("", response_model=TransactionListResponse, dependencies=[Depends(require_role(*ALL_ROLES))])
def list_transactions(
    search: str | None = Query(None, min_length=1, max_length=100),
    direction: TransactionDirection | None = Query(None),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> TransactionListResponse:
    conditions = []
    if direction is not None:
        conditions.append(Transaction.direction == direction)
    if search:
        pattern = f"%{search}%"
        conditions.append(
            or_(
                Transaction.transaction_ref.ilike(pattern),
                Account.account_number.ilike(pattern),
                Customer.customer_ref.ilike(pattern),
                Customer.full_name.ilike(pattern),
            )
        )

    total = db.scalar(_with_owner(select(func.count()).select_from(Transaction)).where(*conditions)) or 0
    rows = db.execute(
        _with_owner(select(Transaction, Account.account_number, Customer))
        .where(*conditions)
        .order_by(Transaction.transaction_date.desc(), Transaction.transaction_ref)
        .limit(limit)
        .offset(offset)
    ).all()

    return TransactionListResponse(
        items=[
            TransactionRow(
                id=transaction.id,
                transaction_ref=transaction.transaction_ref,
                account_number=account_number,
                transaction_date=transaction.transaction_date,
                amount=transaction.amount,
                currency=transaction.currency,
                direction=transaction.direction,
                channel=transaction.channel,
                counterparty_ref=transaction.counterparty_ref,
                description=transaction.description,
                customer_id=customer.id,
                customer_ref=customer.customer_ref,
                customer_name=customer.full_name,
            )
            for transaction, account_number, customer in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
