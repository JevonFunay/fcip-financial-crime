"""A tiny builder for pattern tests: customers, accounts, merchants and transactions
written straight to the test database, so each test states only what it needs."""

import itertools
import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.customer import Customer
from app.models.enums import AccountStatus, EntityType, TransactionDirection
from app.models.merchant import Merchant
from app.models.transaction import Transaction
from app.services.detection import common as c
from app.services.detection.active_rules import active_rule_versions
from app.services.detection.registry import TEMPLATES

M = Decimal(1_000_000)
# Monday 2026-01-05 00:00 in Asia/Jakarta (= Sunday 17:00 UTC); weeks and days line up with it.
MONDAY = c.local_midnight(date(2026, 1, 5))


class World:
    def __init__(self, db: Session):
        self.db = db
        self._n = itertools.count(1)

    def customer(self, ref: str, kind: EntityType = EntityType.INDIVIDUAL) -> Customer:
        row = Customer(customer_ref=ref, full_name=ref, entity_type=kind, country="ID")
        self.db.add(row)
        self.db.flush()
        return row

    def account(self, customer: Customer) -> Account:
        row = Account(account_number=f"ACC-{customer.customer_ref}-{next(self._n)}", customer_id=customer.id,
                      account_type="WALLET", currency="IDR", status=AccountStatus.ACTIVE)
        self.db.add(row)
        self.db.flush()
        return row

    def merchant(self, ref: str, owner: Customer, *, band: str | None = "LT_50K", hours: str | None = "06:00-22:00",
                 mcc: str = "5411") -> Merchant:
        row = Merchant(merchant_ref=ref, customer_id=owner.id, name=ref, mcc=mcc, declared_ticket_band=band,
                       operating_hours=hours, status="ACTIVE")
        self.db.add(row)
        self.db.flush()
        return row

    def tx(self, account: Account, when: datetime, amount: Decimal | int | float, *, direction: str = "DEBIT",
           counterparty: str | None = None, country: str | None = None, merchant: str | None = None,
           device: str | None = None, status: str | None = None, channel: str = "TRANSFER") -> Transaction:
        row = Transaction(
            transaction_ref=f"TXN-{uuid.uuid4().hex[:12]}", account_id=account.id, transaction_date=when,
            amount=Decimal(str(amount)), currency="IDR", direction=TransactionDirection(direction), channel=channel,
            counterparty_ref=counterparty, counterparty_country=country, merchant_ref=merchant, device_ref=device,
            source_status=status,
        )
        self.db.add(row)
        return row

    def done(self) -> None:
        self.db.flush()


def seeded_params(db: Session, pattern_code: str):
    """The parameters of the seeded ACTIVE version, exactly as a live run would read them."""
    [(_, version)] = active_rule_versions(db, pattern_code)
    return TEMPLATES[pattern_code].parameters_from(version)


def run_evaluation(db: Session, pattern_code: str, params=None, **overrides):
    params = params or seeded_params(db, pattern_code)
    if overrides:
        params = replace(params, **overrides)
    return TEMPLATES[pattern_code].evaluate(db, params)
