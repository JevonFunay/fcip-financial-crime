"""Transaction CSV ingestion: valid rows become transactions, invalid rows are quarantined with a reason."""

import csv
import io
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.account import Account
from app.models.enums import IngestionBatchStatus, TransactionDirection
from app.models.ingestion_batch import IngestionBatch
from app.models.processing_log import (
    EVENT_RECONCILIATION_MISMATCH,
    EVENT_RECONCILIATION_OK,
    EVENT_VALIDATION_COMPLETED,
    ProcessingLog,
)
from app.models.quarantine_item import QuarantineItem
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.ingestion import IngestionSummary, QuarantinePreview
from app.services.audit import OBJECT_BATCH, record_transition

REQUIRED_COLUMNS = (
    "transaction_ref",
    "account_number",
    "transaction_date",
    "amount",
    "currency",
    "direction",
    "channel",
)
OPTIONAL_COLUMNS = ("counterparty_ref", "description")

QUARANTINE_PREVIEW_LIMIT = 20
LOOKUP_CHUNK_SIZE = 1000
MAX_AMOUNT = Decimal(10) ** 16  # transaction.amount is NUMERIC(18, 2)

_AMOUNT_PATTERN = re.compile(r"[0-9]+(\.[0-9]{1,2})?")
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")


class IngestionFileError(Exception):
    """The file as a whole cannot be processed (bad header, encoding, malformed CSV)."""


class RowValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ParsedTransaction:
    transaction_ref: str
    account_number: str
    transaction_date: datetime
    amount: Decimal
    currency: str
    direction: TransactionDirection
    channel: str
    counterparty_ref: str | None
    description: str | None


def _parse_amount(value: str) -> Decimal:
    if not _AMOUNT_PATTERN.fullmatch(value):
        raise ValueError("must be a plain positive number with at most 2 decimal places (e.g. 1500000.00)")
    amount = Decimal(value)
    if amount == 0:
        raise ValueError("must be greater than 0")
    if amount >= MAX_AMOUNT:
        raise ValueError("is too large")
    return amount


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(
            "must be an ISO 8601 date or datetime (e.g. 2026-08-03 or 2026-08-03T09:15:00+07:00)"
        ) from None
    # Timestamps without an offset are interpreted as UTC.
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _parse_direction(value: str) -> TransactionDirection:
    try:
        return TransactionDirection(value.upper())
    except ValueError:
        raise ValueError("must be CREDIT or DEBIT") from None


def _parse_currency(value: str) -> str:
    code = value.upper()
    if not _CURRENCY_PATTERN.fullmatch(code):
        raise ValueError("must be a 3-letter currency code (e.g. IDR)")
    return code


def parse_transaction_row(raw: dict[str, Any]) -> ParsedTransaction:
    values = {name: (raw.get(name) or "").strip() for name in REQUIRED_COLUMNS + OPTIONAL_COLUMNS}
    errors = [f"{name} is required" for name in REQUIRED_COLUMNS if not values[name]]

    def parsed(name: str, parser: Callable[[str], Any]) -> Any:
        if not values[name]:
            return None
        try:
            return parser(values[name])
        except ValueError as exc:
            errors.append(f"{name} {exc}")
            return None

    amount = parsed("amount", _parse_amount)
    transaction_date = parsed("transaction_date", _parse_datetime)
    direction = parsed("direction", _parse_direction)
    currency = parsed("currency", _parse_currency)

    if errors:
        raise RowValidationError(errors)

    return ParsedTransaction(
        transaction_ref=values["transaction_ref"],
        account_number=values["account_number"],
        transaction_date=transaction_date,
        amount=amount,
        currency=currency,
        direction=direction,
        channel=values["channel"].upper(),
        counterparty_ref=values["counterparty_ref"] or None,
        description=values["description"] or None,
    )


def _raw_for_storage(raw: dict[Any, Any]) -> dict[str, Any]:
    return {("_extra_fields" if key is None else key): value for key, value in raw.items()}


def _lookup_account_ids(db: Session, account_numbers: set[str]) -> dict[str, uuid.UUID]:
    numbers = sorted(account_numbers)
    found: dict[str, uuid.UUID] = {}
    for start in range(0, len(numbers), LOOKUP_CHUNK_SIZE):
        chunk = numbers[start : start + LOOKUP_CHUNK_SIZE]
        rows = db.execute(select(Account.account_number, Account.id).where(Account.account_number.in_(chunk)))
        found.update({number: account_id for number, account_id in rows})
    return found


def _finalise_batch(
    db: Session,
    batch: IngestionBatch,
    *,
    actor: User | None,
    total_rows: int,
    accepted: int,
    quarantined: int,
) -> None:
    """FR-105 end-of-batch reconciliation. TRD C-02 requires that every input
    row is accounted for; a mismatch marks the batch for follow-up instead of
    letting it finish quietly as COMPLETED."""
    batch.total_rows = total_rows
    batch.accepted_rows = accepted
    batch.quarantined_rows = quarantined
    batch.completed_at = datetime.now(timezone.utc)

    db.add(
        ProcessingLog(
            ingestion_batch_id=batch.id,
            event_code=EVENT_VALIDATION_COMPLETED,
            message=f"Validated {total_rows} row(s): {accepted} accepted, {quarantined} quarantined",
            details={"total_rows": total_rows, "accepted": accepted, "quarantined": quarantined},
        )
    )

    discrepancies: list[str] = []
    if accepted + quarantined != total_rows:
        discrepancies.append(
            f"accepted ({accepted}) + quarantined ({quarantined}) != rows read ({total_rows})"
        )
    if batch.expected_records is not None and batch.expected_records != total_rows:
        discrepancies.append(f"rows read ({total_rows}) != expected_records ({batch.expected_records})")

    if discrepancies:
        batch.status = IngestionBatchStatus.NEEDS_REVIEW
        message = "; ".join(discrepancies)
        db.add(
            ProcessingLog(
                ingestion_batch_id=batch.id,
                event_code=EVENT_RECONCILIATION_MISMATCH,
                message=message,
                details={"discrepancies": discrepancies},
            )
        )
    else:
        batch.status = IngestionBatchStatus.COMPLETED
        message = f"{total_rows} row(s) reconciled"
        db.add(
            ProcessingLog(
                ingestion_batch_id=batch.id,
                event_code=EVENT_RECONCILIATION_OK,
                message=message,
            )
        )

    record_transition(
        db,
        correlation_id=batch.correlation_id,
        actor=actor,
        object_type=OBJECT_BATCH,
        object_id=batch.id,
        from_state=IngestionBatchStatus.REGISTERED.value,
        to_state=batch.status.value,
        reason=message,
    )


def ingest_transactions_csv(
    db: Session,
    *,
    file_name: str,
    content: str,
    batch: IngestionBatch | None = None,
    actor: User | None = None,
) -> IngestionSummary:
    """row_number is spreadsheet-style: the header is row 1, the first data row is row 2.

    `batch` is optional so the seed script and unit tests can ingest without
    registering a batch; uploads through the API always run inside one (FR-101).
    """
    reader = csv.DictReader(io.StringIO(content))
    candidates: list[tuple[int, dict[str, Any], ParsedTransaction]] = []
    quarantined: list[tuple[int, dict[str, Any], str]] = []
    first_seen: dict[str, int] = {}
    total_rows = 0
    row_number = 1

    try:
        if not reader.fieldnames:
            raise IngestionFileError("File is empty or has no header row")
        header = [name.strip().lower() for name in reader.fieldnames]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            raise IngestionFileError(f"Missing required column(s): {', '.join(missing)}")
        reader.fieldnames = header

        for row_number, raw in enumerate(reader, start=2):
            total_rows += 1
            stored = _raw_for_storage(raw)

            if None in raw:
                quarantined.append((row_number, stored, "row has more fields than the header"))
                continue

            try:
                parsed = parse_transaction_row(raw)
            except RowValidationError as exc:
                quarantined.append((row_number, stored, "; ".join(exc.errors)))
                continue

            if parsed.transaction_ref in first_seen:
                reason = (
                    f"duplicate transaction_ref '{parsed.transaction_ref}' "
                    f"within file (first seen on row {first_seen[parsed.transaction_ref]})"
                )
                quarantined.append((row_number, stored, reason))
                continue

            first_seen[parsed.transaction_ref] = row_number
            candidates.append((row_number, stored, parsed))
    except csv.Error as exc:
        raise IngestionFileError(f"Malformed CSV after row {row_number}: {exc}") from exc

    account_ids = _lookup_account_ids(db, {parsed.account_number for _, _, parsed in candidates})
    to_insert: list[tuple[int, dict[str, Any], ParsedTransaction, uuid.UUID]] = []
    for row_number, stored, parsed in candidates:
        account_id = account_ids.get(parsed.account_number)
        if account_id is None:
            quarantined.append((row_number, stored, f"account_number '{parsed.account_number}' does not exist"))
            continue
        to_insert.append((row_number, stored, parsed, account_id))

    inserted_refs: set[str] = set()
    if to_insert:
        table = Transaction.__table__
        statement = (
            pg_insert(table)
            .on_conflict_do_nothing(index_elements=["transaction_ref"])
            .returning(table.c.transaction_ref)
        )
        result = db.execute(
            statement,
            [
                {
                    "transaction_ref": parsed.transaction_ref,
                    "account_id": account_id,
                    "transaction_date": parsed.transaction_date,
                    "amount": parsed.amount,
                    "currency": parsed.currency,
                    "direction": parsed.direction,
                    "channel": parsed.channel,
                    "counterparty_ref": parsed.counterparty_ref,
                    "description": parsed.description,
                    "ingestion_batch_id": batch.id if batch is not None else None,
                }
                for _, _, parsed, account_id in to_insert
            ],
        )
        inserted_refs = set(result.scalars())

    for row_number, stored, parsed, _ in to_insert:
        if parsed.transaction_ref not in inserted_refs:
            reason = f"transaction_ref '{parsed.transaction_ref}' already exists (previously ingested)"
            quarantined.append((row_number, stored, reason))

    quarantined.sort(key=lambda item: item[0])
    db.add_all(
        QuarantineItem(
            source_file_name=file_name,
            row_number=number,
            raw_row=stored,
            error_reason=reason,
            ingestion_batch_id=batch.id if batch is not None else None,
        )
        for number, stored, reason in quarantined
    )

    if batch is not None:
        _finalise_batch(
            db,
            batch,
            actor=actor,
            total_rows=total_rows,
            accepted=len(inserted_refs),
            quarantined=len(quarantined),
        )
    db.commit()

    return IngestionSummary(
        file_name=file_name,
        total_rows=total_rows,
        accepted=len(inserted_refs),
        quarantined=len(quarantined),
        batch_ref=batch.batch_ref if batch is not None else None,
        batch_status=batch.status.value if batch is not None else None,
        quarantine_preview=[
            QuarantinePreview(row_number=number, error_reason=reason)
            for number, _, reason in quarantined[:QUARANTINE_PREVIEW_LIMIT]
        ],
    )
