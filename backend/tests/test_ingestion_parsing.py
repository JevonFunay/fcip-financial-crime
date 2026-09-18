from datetime import timedelta, timezone
from decimal import Decimal

import pytest

from app.models.enums import TransactionDirection
from app.services.ingestion import RowValidationError, parse_transaction_row


def _row(**overrides: str) -> dict[str, str]:
    row = {
        "transaction_ref": "TXN-1",
        "account_number": "ACC-1",
        "transaction_date": "2026-08-03T09:15:00Z",
        "amount": "450000000.00",
        "currency": "IDR",
        "direction": "CREDIT",
        "channel": "CASH",
        "counterparty_ref": "",
        "description": "",
    }
    row.update(overrides)
    return row


def test_valid_row_is_parsed_and_normalized():
    parsed = parse_transaction_row(
        _row(currency="idr", direction="credit", channel="cash", counterparty_ref=" CP-1 ", description="Deposit")
    )

    assert parsed.amount == Decimal("450000000.00")
    assert parsed.currency == "IDR"
    assert parsed.direction is TransactionDirection.CREDIT
    assert parsed.channel == "CASH"
    assert parsed.counterparty_ref == "CP-1"
    assert parsed.description == "Deposit"


def test_optional_columns_default_to_none():
    parsed = parse_transaction_row(_row())

    assert parsed.counterparty_ref is None
    assert parsed.description is None


@pytest.mark.parametrize("amount", ["350000000", "0.01", "99.5", "500000000.00"])
def test_valid_amounts_accepted(amount):
    assert parse_transaction_row(_row(amount=amount)).amount == Decimal(amount)


@pytest.mark.parametrize(
    "amount",
    ["abc", "-5", "0", "0.00", "1.234", "1e3", "NaN", "Infinity", "1,000.00", "10000000000000000"],
)
def test_invalid_amounts_rejected(amount):
    with pytest.raises(RowValidationError) as exc_info:
        parse_transaction_row(_row(amount=amount))

    assert exc_info.value.errors[0].startswith("amount ")


@pytest.mark.parametrize("value", ["2026-02-30", "not-a-date", "31/08/2026", "2026-13-01"])
def test_invalid_dates_rejected(value):
    with pytest.raises(RowValidationError) as exc_info:
        parse_transaction_row(_row(transaction_date=value))

    assert exc_info.value.errors[0].startswith("transaction_date ")


def test_date_only_and_naive_datetimes_are_treated_as_utc():
    date_only = parse_transaction_row(_row(transaction_date="2026-08-25")).transaction_date
    naive = parse_transaction_row(_row(transaction_date="2026-08-25T10:00:00")).transaction_date

    assert date_only.utcoffset() == timedelta(0) and date_only.hour == 0
    assert naive.utcoffset() == timedelta(0) and naive.hour == 10


def test_explicit_offset_is_preserved():
    parsed = parse_transaction_row(_row(transaction_date="2026-08-26T16:00:00+07:00")).transaction_date

    assert parsed.utcoffset() == timedelta(hours=7)
    assert parsed.astimezone(timezone.utc).hour == 9


def test_invalid_direction_rejected():
    with pytest.raises(RowValidationError) as exc_info:
        parse_transaction_row(_row(direction="SIDEWAYS"))

    assert exc_info.value.errors == ["direction must be CREDIT or DEBIT"]


@pytest.mark.parametrize("currency", ["ID", "IDRR", "12A", "I D"])
def test_invalid_currency_rejected(currency):
    with pytest.raises(RowValidationError) as exc_info:
        parse_transaction_row(_row(currency=currency))

    assert exc_info.value.errors[0].startswith("currency ")


@pytest.mark.parametrize("column", ["transaction_ref", "account_number", "transaction_date", "amount", "currency", "direction", "channel"])
def test_required_columns_cannot_be_blank(column):
    with pytest.raises(RowValidationError) as exc_info:
        parse_transaction_row(_row(**{column: "  "}))

    assert exc_info.value.errors == [f"{column} is required"]


def test_all_problems_in_a_row_are_reported_together():
    with pytest.raises(RowValidationError) as exc_info:
        parse_transaction_row(_row(amount="abc", currency="", direction="SIDEWAYS"))

    assert len(exc_info.value.errors) == 3
