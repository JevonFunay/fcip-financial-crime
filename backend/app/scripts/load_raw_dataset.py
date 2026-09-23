"""Bridge the generated raw source files into what the app can consume today.

The raw contract (TRD §6.1) is eight files with the full banking column set.
The walking skeleton only models customers, accounts and transactions, and its
ingestion endpoint takes a narrower CSV, so this script:

1. loads `customers.csv` / `business_customers.csv` / `accounts.csv` into the
   `customer` and `account` master-data tables (which are seeded, not ingested,
   at this stage — see the README's known simplifications), and
2. projects `transactions.csv` onto the ingestion CSV shape, so it can be
   uploaded through the real `POST /ingestion/transactions` endpoint and go
   through batch registration, validation and quarantine like any other file.

What is dropped in step 2 is the gap between the skeleton and the full pipeline,
and the script prints it rather than hiding it.

Run:
    python -m app.scripts.load_raw_dataset --profile small
    python -m app.scripts.load_raw_dataset --profile small --master-data-only
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.account import Account
from app.models.customer import Customer
from app.models.enums import AccountStatus, EntityType
from app.scripts.generate_raw_dataset import DEFAULT_OUT

# The app's ingestion contract (see app/services/ingestion.py).
APP_COLUMNS = (
    "transaction_ref", "account_number", "transaction_date", "amount",
    "currency", "direction", "channel", "counterparty_ref", "description",
)
DIRECTION_MAP = {"IN": "CREDIT", "OUT": "DEBIT"}


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_master_data(db: Session, raw_dir: Path) -> tuple[int, int]:
    """Idempotent: existing customer_ref / account_number rows are left alone."""
    customers = _read(raw_dir / "customers.csv")
    businesses = _read(raw_dir / "business_customers.csv")
    accounts = _read(raw_dir / "accounts.csv")

    existing_customers = set(db.scalars(select(Customer.customer_ref)))
    new_customers: list[Customer] = []
    for row in customers:
        if row["source_customer_id"] in existing_customers:
            continue
        new_customers.append(Customer(
            customer_ref=row["source_customer_id"],
            full_name=row["full_name"],
            entity_type=EntityType.INDIVIDUAL,
            id_number=row["national_id_reference"],
            date_of_birth=date.fromisoformat(row["date_of_birth"]) if row["date_of_birth"] else None,
            country=row["country"] or None,
        ))
    for row in businesses:
        if row["source_business_id"] in existing_customers:
            continue
        new_customers.append(Customer(
            customer_ref=row["source_business_id"],
            full_name=row["legal_name"],
            entity_type=EntityType.BUSINESS,
            id_number=row["registration_number"],
            date_of_birth=None,
            country=row["country"] or None,
        ))
    db.add_all(new_customers)
    db.flush()

    owner_ids = {ref: cid for ref, cid in db.execute(select(Customer.customer_ref, Customer.id))}
    existing_accounts = set(db.scalars(select(Account.account_number)))
    new_accounts: list[Account] = []
    skipped_accounts = 0
    for row in accounts:
        if row["source_account_id"] in existing_accounts:
            continue
        owner = owner_ids.get(row["owner_source_id"])
        if owner is None:
            skipped_accounts += 1
            continue
        new_accounts.append(Account(
            account_number=row["source_account_id"],
            customer_id=owner,
            account_type=row["account_type"],
            currency=row["currency"],
            status=AccountStatus.ACTIVE if row["status"] != "CLOSED" else AccountStatus.CLOSED,
            opened_date=date.fromisoformat(row["opened_date"]) if row["opened_date"] else None,
        ))
    db.add_all(new_accounts)
    db.commit()

    if skipped_accounts:
        print(f"  ! {skipped_accounts} account(s) skipped: owner not present in this dataset")
    return len(new_customers), len(new_accounts)


def project_transactions(raw_dir: Path, out_path: Path) -> dict[str, int]:
    """Project the raw transaction contract onto the app's ingestion CSV.

    Rows are passed through as-is, including the deliberately malformed ones —
    quarantining them is the point (TRD §11.4), so nothing is cleaned here.
    """
    rows = _read(raw_dir / "transactions.csv")
    stats = {"total": len(rows), "written": 0, "non_idr": 0}

    with out_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=APP_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            # The skeleton has no FX table, so a non-IDR row cannot be valued.
            # It is counted and dropped here rather than silently mis-valued.
            if row["currency_original"] not in ("IDR", "") and len(row["currency_original"]) == 3:
                stats["non_idr"] += 1
                continue
            writer.writerow({
                "transaction_ref": row["source_transaction_reference"],
                "account_number": row["source_account_id"],
                "transaction_date": row["value_datetime"],
                "amount": row["amount_original"],
                "currency": row["currency_original"],
                "direction": DIRECTION_MAP.get(row["direction"], row["direction"]),
                "channel": row["channel"],
                "counterparty_ref": row["counterparty_reference"],
                "description": " ".join(part for part in (
                    row["transaction_type"], row["counterparty_name"],
                    f"[{row['counterparty_country']}]" if row["counterparty_country"] not in ("", "ID") else "",
                ) if part).strip(),
            })
            stats["written"] += 1
    return stats


DROPPED_FIELDS = (
    "business_date", "source_merchant_id", "source_device_id", "ip_address",
    "status_from_source", "transaction_type (folded into description)",
    "counterparty_name (folded into description)", "counterparty_country (folded into description)",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the generated raw dataset into the walking skeleton.")
    parser.add_argument("--profile", default="small", help="which generated profile directory to read")
    parser.add_argument("--raw-dir", type=Path, default=None, help="override the raw dataset directory")
    parser.add_argument("--master-data-only", action="store_true", help="load customers/accounts, skip the CSV projection")
    args = parser.parse_args()

    raw_dir = args.raw_dir or (DEFAULT_OUT / args.profile)
    if not (raw_dir / "customers.csv").exists():
        raise SystemExit(f"no generated dataset at {raw_dir} — run: python -m app.scripts.generate_raw_dataset --profile {args.profile}")

    print(f"raw dataset: {raw_dir}")
    with SessionLocal() as db:
        customers, accounts = load_master_data(db, raw_dir)
    print(f"  master data loaded: {customers:,} customer(s), {accounts:,} account(s)")

    if args.master_data_only:
        return

    out_path = raw_dir / "transactions_app_format.csv"
    stats = project_transactions(raw_dir, out_path)
    print(f"  projected {stats['written']:,} of {stats['total']:,} transaction(s) -> {out_path}")
    if stats["non_idr"]:
        print(f"  ! {stats['non_idr']:,} non-IDR row(s) dropped: the skeleton has no FX table yet")
    print("  ! not carried across (no column in the skeleton's ingestion contract):")
    for field in DROPPED_FIELDS:
        print(f"      - {field}")
    print("\nNext: upload that file as ROLE_DATA_OPS, from the Overview page or:")
    print(f'  curl -X POST http://localhost:8000/ingestion/transactions -H "Authorization: Bearer <token>" \\')
    print(f'    -F "file=@{out_path}" -F "source_system=NDP_WALLET_CORE" -F "business_date=<YYYY-MM-DD>"')


if __name__ == "__main__":
    main()
