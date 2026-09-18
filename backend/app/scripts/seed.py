"""Creates dev users (one per role) and synthetic customers/accounts. Idempotent — skips rows that already exist.

Run via: docker compose exec backend python -m app.scripts.seed
"""

from datetime import date

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.database import SessionLocal
from app.models.account import Account
from app.models.customer import Customer
from app.models.enums import EntityType, UserRole
from app.models.user import User

DEV_PASSWORD = "DevPassword123!"

SEED_USERS = [
    ("dataops@fcip.internal", "DataOps User", UserRole.ROLE_DATA_OPS),
    ("analyst@fcip.internal", "Analyst User", UserRole.ROLE_ANALYST),
    ("triage@fcip.internal", "Triage User", UserRole.ROLE_TRIAGE),
    ("investigator@fcip.internal", "Investigator User", UserRole.ROLE_INVESTIGATOR),
]

# customer_ref, full_name, entity_type, id_number, date_of_birth, country
SEED_CUSTOMERS = [
    ("CUST-001", "Budi Santoso", EntityType.INDIVIDUAL, "SYN-ID-0001", date(1985, 3, 14), "ID"),
    ("CUST-002", "Siti Rahayu", EntityType.INDIVIDUAL, "SYN-ID-0002", date(1990, 7, 2), "ID"),
    ("CUST-003", "PT Maju Bersama", EntityType.BUSINESS, "SYN-ID-0003", None, "ID"),
    ("CUST-004", "Agus Pratama", EntityType.INDIVIDUAL, "SYN-ID-0004", date(1978, 11, 23), "ID"),
    ("CUST-005", "CV Sumber Rejeki", EntityType.BUSINESS, "SYN-ID-0005", None, "ID"),
    ("CUST-006", "Dewi Lestari", EntityType.INDIVIDUAL, "SYN-ID-0006", date(1992, 1, 30), "ID"),
]

# account_number, customer_ref, account_type, currency, opened_date
SEED_ACCOUNTS = [
    ("ACC-1001", "CUST-001", "SAVINGS", "IDR", date(2019, 5, 1)),
    ("ACC-1002", "CUST-001", "CHECKING", "IDR", date(2021, 2, 15)),
    ("ACC-2001", "CUST-002", "SAVINGS", "IDR", date(2020, 8, 20)),
    ("ACC-3001", "CUST-003", "BUSINESS", "IDR", date(2018, 1, 10)),
    ("ACC-4001", "CUST-004", "SAVINGS", "IDR", date(2017, 6, 5)),
    ("ACC-5001", "CUST-005", "BUSINESS", "IDR", date(2022, 3, 3)),
    ("ACC-5002", "CUST-005", "BUSINESS", "USD", date(2022, 3, 3)),
    ("ACC-6001", "CUST-006", "SAVINGS", "IDR", date(2023, 9, 12)),
]


def seed_users(db: Session) -> None:
    for email, full_name, role in SEED_USERS:
        if db.query(User).filter(User.email == email).one_or_none() is not None:
            print(f"skip (already exists): {email}")
            continue
        db.add(User(email=email, hashed_password=hash_password(DEV_PASSWORD), full_name=full_name, role=role))
        print(f"created: {email} ({role.value}) password={DEV_PASSWORD}")
    db.commit()


def seed_master_data(db: Session) -> None:
    customer_ids = {}
    for customer_ref, full_name, entity_type, id_number, date_of_birth, country in SEED_CUSTOMERS:
        customer = db.query(Customer).filter(Customer.customer_ref == customer_ref).one_or_none()
        if customer is None:
            customer = Customer(
                customer_ref=customer_ref,
                full_name=full_name,
                entity_type=entity_type,
                id_number=id_number,
                date_of_birth=date_of_birth,
                country=country,
            )
            db.add(customer)
            db.flush()
            print(f"created customer: {customer_ref} ({full_name})")
        customer_ids[customer_ref] = customer.id

    for account_number, customer_ref, account_type, currency, opened_date in SEED_ACCOUNTS:
        if db.query(Account).filter(Account.account_number == account_number).one_or_none() is not None:
            continue
        db.add(
            Account(
                account_number=account_number,
                customer_id=customer_ids[customer_ref],
                account_type=account_type,
                currency=currency,
                opened_date=opened_date,
            )
        )
        print(f"created account: {account_number} ({customer_ref}, {currency})")
    db.commit()


def run() -> None:
    db = SessionLocal()
    try:
        seed_users(db)
        seed_master_data(db)
    finally:
        db.close()


if __name__ == "__main__":
    run()
