"""CSV or Parquet uploads become the same text cells (app/services/tabular.py)."""

import datetime as dt
import decimal
import io

import pandas as pd
import pytest

from app.services.tabular import (
    FORMAT_CSV,
    FORMAT_PARQUET,
    TableFileError,
    detect_format,
    parquet_to_csv_text,
    parquet_to_frame,
)


def _parquet(frame: pd.DataFrame, **options) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, **options)
    return buffer.getvalue()


def test_format_is_recognised_from_content_not_name() -> None:
    assert detect_format(b"a,b\n1,2\n") == FORMAT_CSV
    assert detect_format(_parquet(pd.DataFrame({"a": [1]}))) == FORMAT_PARQUET
    assert detect_format(b"PAR1") == FORMAT_CSV  # too short to be a Parquet file


def test_typed_parquet_cells_become_the_text_a_csv_would_hold() -> None:
    frame = pd.DataFrame({
        "id": ["A1", None],
        "amount": [1112867.0, 94611.5],
        "tiny": [0.0000001, float("nan")],
        "exact": [decimal.Decimal("1112867.00"), None],
        "count": pd.array([3, None], dtype="Int64"),
        "flag": [True, None],
        "at_wib": pd.to_datetime(["2026-04-01T17:57:37+07:00", None], utc=True).tz_convert("Asia/Jakarta"),
        "at_naive": pd.to_datetime(["2026-04-01 17:57:37", None]),
        "day": [dt.date(2026, 4, 1), None],
    })
    cells = parquet_to_frame(_parquet(frame, index=True))
    # pandas' own index column is not data.
    assert list(cells.columns) == list(frame.columns)
    # Plain positional numbers, exact decimals, offsets only where stored, dates keep day precision.
    assert cells.iloc[0].tolist() == ["A1", "1112867", "0.0000001", "1112867.00", "3", "true",
                                      "2026-04-01T17:57:37+07:00", "2026-04-01T17:57:37", "2026-04-01"]
    # Missing values (None, NaN, NaT) become empty cells, as in a CSV.
    assert cells.iloc[1].tolist() == ["", "94611.5"] + [""] * 7


def test_csv_text_keeps_rows_and_quoting() -> None:
    text = parquet_to_csv_text(_parquet(pd.DataFrame({"a": ["x,y", "plain"], "b": [1, 2]}), index=False))
    assert text == 'a,b\n"x,y",1\nplain,2\n'


def test_unreadable_parquet_is_refused_with_a_reason() -> None:
    with pytest.raises(TableFileError, match="Parquet"):
        parquet_to_frame(b"PAR1" + b"\x00" * 32 + b"PAR1")


def test_transaction_upload_accepts_parquet_and_validates_it_like_csv(client, db, auth_headers) -> None:
    from app.models.account import Account
    from app.models.customer import Customer
    from app.models.enums import EntityType, UserRole
    from app.models.quarantine_item import QuarantineItem

    customer = Customer(customer_ref="CUST-PQ", full_name="Parquet Test", entity_type=EntityType.INDIVIDUAL)
    db.add(customer)
    db.flush()
    db.add(Account(account_number="ACC-PQ1", customer_id=customer.id, account_type="SAVINGS", currency="IDR"))
    db.commit()
    rows = pd.DataFrame({
        "transaction_ref": ["PQ-1", "PQ-2", "PQ-3"],
        "account_number": ["ACC-PQ1"] * 3,
        "transaction_date": ["2026-08-03T09:15:00Z", "2026-08-04", "2026-08-05"],
        "amount": ["450000000.00", "15000000", "-5"],
        "currency": ["IDR"] * 3,
        "direction": ["CREDIT", "DEBIT", "DEBIT"],
        "channel": ["CASH", "TRANSFER", "TRANSFER"],
    })
    response = client.post("/ingestion/transactions", headers=auth_headers(UserRole.ROLE_DATA_OPS),
                           files={"file": ("daily.parquet", _parquet(rows, index=False), "application/octet-stream")})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["total_rows"], body["accepted"], body["quarantined"]) == (3, 2, 1)
    assert "amount" in db.query(QuarantineItem).one().error_reason
