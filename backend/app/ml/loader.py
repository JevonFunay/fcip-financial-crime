"""Reads a raw dataset for the feature library, and nothing else.

Only the files and columns in ALLOWED are ever opened. Ground truth
(labels.csv, label_transactions.csv), the generation report, the scenario
catalogue and seeds.json are not in it, so the feature library cannot see the
answers. Customer and business master data are not read either: the only
static facts a feature uses are the account's owner and opening date, and a
merchant's category (MCC) and declared hours.

Transactions pass exactly the validation the ingestion endpoint applies — the
bridge's currency rule, then parse_transaction_row, first-wins duplicate
references, and a known account — so features are computed on the rows the
app would store (training/serving parity). The rows ingestion would quarantine
are counted, not used.
"""

from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

from app.ml.feature_set import JAKARTA
from app.scripts.load_raw_dataset import _ISO_CURRENCY, DIRECTION_MAP
from app.services.ingestion import RowValidationError, parse_transaction_row

ALLOWED: dict[str, tuple[str, ...]] = {
    "accounts.csv": ("source_account_id", "owner_source_id", "owner_type", "opened_date"),
    "merchants.csv": ("source_merchant_id", "source_business_id", "mcc", "operating_hours_declared"),
    "devices.csv": ("source_device_id", "is_emulator", "is_rooted"),
    "transactions.csv": (
        "source_transaction_reference", "source_account_id", "direction", "amount_original", "currency_original",
        "value_datetime", "channel", "transaction_type", "counterparty_reference", "counterparty_country",
        "source_merchant_id", "source_device_id", "status_from_source",
    ),
}


@dataclass(frozen=True, slots=True)
class Txn:
    ref: str
    account: str
    entity: str
    ts: float              # epoch seconds
    amount: float
    out: bool
    channel: str
    txn_type: str
    counterparty: str      # "" when missing
    counterparty_country: str
    merchant: str          # "" when not a merchant payment
    device: str            # "" when missing
    reversed: bool


@dataclass(frozen=True)
class Account:
    entity: str
    is_business: bool
    opened_ts: float


@dataclass(frozen=True)
class Merchant:
    business: str
    mcc: str
    hours: tuple[int, int] | None  # minutes of day (open, close); None = always open


@dataclass
class Dataset:
    transactions: list[Txn]
    accounts: dict[str, Account]
    merchants: dict[str, Merchant]
    devices: dict[str, tuple[bool, bool]]  # emulator, rooted
    rejected: Counter = field(default_factory=Counter)

    @property
    def entities(self) -> dict[str, list[Txn]]:
        by_entity: dict[str, list[Txn]] = {}
        for txn in self.transactions:
            by_entity.setdefault(txn.entity, []).append(txn)
        return by_entity


def _read(folder: Path, name: str) -> list[dict[str, str]]:
    """The only way this module opens a file: allow-listed name, allow-listed columns."""
    columns = ALLOWED[name]
    with (folder / name).open(encoding="utf-8") as handle:
        return [{column: row[column] for column in columns} for row in csv.DictReader(handle)]


def _hours(value: str) -> tuple[int, int] | None:
    try:
        opens, closes = value.split("-")
        start = int(opens[:2]) * 60 + int(opens[3:5])
        end = int(closes[:2]) * 60 + int(closes[3:5])
    except (ValueError, IndexError):
        return None
    return None if (start, end) == (0, 23 * 60 + 59) else (start, end)


def _midnight(day: str) -> float:
    try:
        return datetime.combine(datetime.fromisoformat(day).date(), time.min, JAKARTA).timestamp()
    except ValueError:
        return float("nan")


def load_dataset(folder: Path) -> Dataset:
    accounts = {
        row["source_account_id"]: Account(row["owner_source_id"], row["owner_type"] == "BUSINESS",
                                          _midnight(row["opened_date"]))
        for row in _read(folder, "accounts.csv")
    }
    merchants = {
        row["source_merchant_id"]: Merchant(row["source_business_id"], row["mcc"],
                                            _hours(row["operating_hours_declared"]))
        for row in _read(folder, "merchants.csv")
    }
    devices = {row["source_device_id"]: (row["is_emulator"] == "true", row["is_rooted"] == "true")
               for row in _read(folder, "devices.csv")}

    rejected: Counter = Counter()
    seen: set[str] = set()
    transactions: list[Txn] = []
    for row in _read(folder, "transactions.csv"):
        currency = row["currency_original"]
        # The bridge's rule: a well-formed foreign code cannot be valued without
        # an FX table, so the row never reaches ingestion.
        if _ISO_CURRENCY.fullmatch(currency) and currency != "IDR":
            rejected["non_idr"] += 1
            continue
        try:
            parsed = parse_transaction_row({
                "transaction_ref": row["source_transaction_reference"],
                "account_number": row["source_account_id"],
                "transaction_date": row["value_datetime"],
                "amount": row["amount_original"],
                "currency": currency,
                "direction": DIRECTION_MAP.get(row["direction"], row["direction"]),
                "channel": row["channel"],
                "counterparty_ref": row["counterparty_reference"],
            })
        except RowValidationError:
            rejected["invalid"] += 1
            continue
        if parsed.transaction_ref in seen:
            rejected["duplicate"] += 1
            continue
        seen.add(parsed.transaction_ref)
        account = accounts.get(parsed.account_number)
        if account is None:
            rejected["unknown_account"] += 1
            continue
        transactions.append(Txn(
            ref=parsed.transaction_ref,
            account=parsed.account_number,
            entity=account.entity,
            ts=parsed.transaction_date.timestamp(),
            amount=float(parsed.amount),
            out=parsed.direction.value == "DEBIT",
            channel=parsed.channel,
            txn_type=row["transaction_type"],
            counterparty=parsed.counterparty_ref or "",
            counterparty_country=row["counterparty_country"],
            merchant=row["source_merchant_id"],
            device=row["source_device_id"],
            reversed=row["status_from_source"] == "REVERSED",
        ))
    return Dataset(transactions, accounts, merchants, devices, rejected)
