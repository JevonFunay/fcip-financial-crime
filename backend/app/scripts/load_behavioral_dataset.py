"""Load account observation proxies, without inventing customer identities.

    python -m app.scripts.load_behavioral_dataset --csv data.csv --profile kaggle_bank_v1
    python -m app.scripts.load_behavioral_dataset --csv fresh.csv --profile fcip_canonical_v1 --ingest

Default mode prepares master accounts only for a subsequent Data Ops upload.
Currency is retained, never converted. Demographics remain transaction-level
observations and are not promoted to identity facts. No model is trained here.
"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.ml.behavioral.standardize import CANONICAL_COLUMNS, normalize_csv
from app.models.account import Account
from app.models.customer import Customer
from app.models.enums import AccountStatus, EntityType
from app.services.ingestion import LOOKUP_CHUNK_SIZE, ingest_transactions_csv


def load_account_proxies(db: Session, frame: pd.DataFrame) -> int:
    """One technical observation proxy per source account; no inferred person."""
    currencies_by_account = {}
    for number, observations in frame.groupby("account_number", sort=True):
        currencies = observations.currency.dropna().unique()
        if len(currencies) != 1:
            raise ValueError(f"{number}: account has ambiguous currency; no FX or account split is inferred")
        currencies_by_account[number] = str(currencies[0])
    numbers = list(currencies_by_account)
    existing = {}
    for start in range(0, len(numbers), LOOKUP_CHUNK_SIZE):
        rows = db.execute(select(Account.account_number, Account.currency, Account.account_type, Customer.customer_ref)
                          .join(Customer, Account.customer_id == Customer.id)
                          .where(Account.account_number.in_(numbers[start:start + LOOKUP_CHUNK_SIZE])))
        existing.update({row.account_number: row for row in rows})
    missing = []
    for number, currency in currencies_by_account.items():
        if number in existing:
            account = existing[number]
            if account.currency != currency:
                raise ValueError(f"{number}: existing master currency differs; no overwrite or FX is inferred")
            if account.account_type != "OBSERVATION_PROXY" or account.customer_ref != f"OBS-{number}":
                raise ValueError(f"{number}: an existing non-proxy account cannot establish source identity")
            continue
        missing.append(number)
    references = [f"OBS-{number}" for number in missing]
    customers = {}
    for start in range(0, len(references), LOOKUP_CHUNK_SIZE):
        for customer in db.scalars(select(Customer).where(
            Customer.customer_ref.in_(references[start:start + LOOKUP_CHUNK_SIZE]),
        )):
            customers[customer.customer_ref] = customer
    account_rows = []
    for number in missing:
        reference = f"OBS-{number}"
        customer = customers.get(reference)
        if customer is None:
            customer = Customer(id=uuid.uuid4(), customer_ref=reference, full_name=str(number),
                                entity_type=EntityType.INDIVIDUAL)
            # INDIVIDUAL satisfies the existing technical schema only. No DOB,
            # nationality, identification number or real-world person is inferred.
            db.add(customer)
        elif (customer.full_name != str(number) or customer.id_number is not None or customer.country is not None
              or customer.date_of_birth is not None or customer.entity_type is not EntityType.INDIVIDUAL):
            raise ValueError(f"{number}: existing customer reference is not an anonymous observation proxy")
        account_rows.append(Account(account_number=number, customer_id=customer.id, account_type="OBSERVATION_PROXY",
                                    currency=currencies_by_account[number], status=AccountStatus.ACTIVE, opened_date=None))
    db.flush()
    db.add_all(account_rows)
    db.commit()
    return len(account_rows)


def canonical_csv(frame: pd.DataFrame) -> str:
    """The web upload contract contains observations, never evaluation labels."""
    return frame.loc[:, CANONICAL_COLUMNS].to_csv(index=False, lineterminator="\n", na_rep="")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--profile", default="fcip_canonical_v1")
    parser.add_argument("--canonical-out", type=Path)
    parser.add_argument("--master-data-only", action="store_true", help="Explicit alias for the default mode")
    parser.add_argument("--ingest", action="store_true", help="Also ingest accepted normalized rows; default is masters only")
    args = parser.parse_args()
    if args.master_data_only and args.ingest:
        parser.error("--master-data-only and --ingest are mutually exclusive")
    if args.canonical_out and args.canonical_out.exists():
        raise SystemExit(f"Refusing to overwrite {args.canonical_out}")
    normalized = normalize_csv(args.csv, args.profile)
    content = canonical_csv(normalized.frame)
    if args.canonical_out:
        args.canonical_out.parent.mkdir(parents=True, exist_ok=True)
        args.canonical_out.write_text(content, encoding="utf-8")
    with SessionLocal() as db:
        created = load_account_proxies(db, normalized.frame)
        summary = (ingest_transactions_csv(db, file_name=args.csv.name, content=content).model_dump()
                   if args.ingest else None)
    print(json.dumps({"normalization": normalized.report, "account_proxies_created": created,
                      "master_data_only": not args.ingest, "ingestion": summary,
                      "quarantine_preview": normalized.quarantine.head(20).to_dict("records")}, default=str))


if __name__ == "__main__":
    main()
