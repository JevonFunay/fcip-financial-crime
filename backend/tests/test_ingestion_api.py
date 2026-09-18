from pathlib import Path

import pytest

from app.models.account import Account
from app.models.customer import Customer
from app.models.enums import EntityType, UserRole
from app.models.quarantine_item import QuarantineItem
from app.models.transaction import Transaction
from app.scripts.seed import seed_master_data

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "transactions_sample.csv"
HEADER = "transaction_ref,account_number,transaction_date,amount,currency,direction,channel,counterparty_ref,description"


@pytest.fixture()
def account(db):
    customer = Customer(customer_ref="CUST-T", full_name="Test Customer", entity_type=EntityType.INDIVIDUAL)
    db.add(customer)
    db.flush()
    acc = Account(account_number="ACC-T1", customer_id=customer.id, account_type="SAVINGS", currency="IDR")
    db.add(acc)
    db.commit()
    return acc


def _upload(client, headers, content: str | bytes, filename: str = "upload.csv"):
    data = content.encode() if isinstance(content, str) else content
    return client.post("/ingestion/transactions", headers=headers, files={"file": (filename, data, "text/csv")})


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def test_upload_requires_authentication(client):
    response = client.post("/ingestion/transactions", files={"file": ("a.csv", b"x", "text/csv")})

    assert response.status_code == 401


@pytest.mark.parametrize("role", [UserRole.ROLE_ANALYST, UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR])
def test_upload_forbidden_for_non_data_ops_roles(client, auth_headers, role):
    response = _upload(client, auth_headers(role), _csv())

    assert response.status_code == 403


def test_valid_rows_are_stored_with_normalized_values(client, db, auth_headers, account):
    csv_text = _csv(
        "TXN-1,ACC-T1,2026-08-03T09:15:00Z,450000000.00,idr,credit,cash,CP-1,Cash deposit",
        "TXN-2,ACC-T1,2026-08-04,15000000,IDR,DEBIT,TRANSFER,,",
    )

    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), csv_text)

    assert response.status_code == 200
    assert response.json() == {
        "file_name": "upload.csv",
        "total_rows": 2,
        "accepted": 2,
        "quarantined": 0,
        "quarantine_preview": [],
    }
    first = db.query(Transaction).filter_by(transaction_ref="TXN-1").one()
    assert first.account_id == account.id
    assert (first.currency, first.channel, first.direction.value) == ("IDR", "CASH", "CREDIT")
    assert first.counterparty_ref == "CP-1"
    assert db.query(QuarantineItem).count() == 0


def test_invalid_rows_are_quarantined_with_reason_and_raw_values(client, db, auth_headers, account):
    csv_text = _csv(
        "TXN-1,ACC-T1,2026-08-03T09:15:00Z,450000000.00,IDR,CREDIT,CASH,,",
        "TXN-2,ACC-T1,2026-08-03T09:15:00Z,abc,IDR,CREDIT,CASH,,bad amount",
        "TXN-3,ACC-T1,2026-02-30,10.00,IDR,CREDIT,CASH,,bad date",
    )

    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), csv_text, filename="dirty.csv")

    body = response.json()
    assert (body["total_rows"], body["accepted"], body["quarantined"]) == (3, 1, 2)
    assert [item["row_number"] for item in body["quarantine_preview"]] == [3, 4]

    items = db.query(QuarantineItem).order_by(QuarantineItem.row_number).all()
    assert [item.source_file_name for item in items] == ["dirty.csv", "dirty.csv"]
    assert items[0].error_reason.startswith("amount must be")
    assert items[0].raw_row["transaction_ref"] == "TXN-2"
    assert items[0].raw_row["amount"] == "abc"
    assert items[1].error_reason.startswith("transaction_date must be")
    assert db.query(Transaction).count() == 1


def test_unknown_account_is_quarantined(client, db, auth_headers, account):
    csv_text = _csv("TXN-1,ACC-NOPE,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,")

    body = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), csv_text).json()

    assert (body["accepted"], body["quarantined"]) == (0, 1)
    assert body["quarantine_preview"][0]["error_reason"] == "account_number 'ACC-NOPE' does not exist"
    assert db.query(Transaction).count() == 0


def test_duplicate_within_file_is_quarantined(client, db, auth_headers, account):
    csv_text = _csv(
        "TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,",
        "TXN-1,ACC-T1,2026-08-04T09:15:00Z,20.00,IDR,CREDIT,CASH,,",
    )

    body = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), csv_text).json()

    assert (body["accepted"], body["quarantined"]) == (1, 1)
    assert "within file (first seen on row 2)" in body["quarantine_preview"][0]["error_reason"]
    assert str(db.query(Transaction).one().amount) == "10.00"


def test_reuploading_the_same_file_quarantines_existing_transactions(client, db, auth_headers, account):
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    csv_text = _csv(
        "TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,",
        "TXN-2,ACC-T1,2026-08-04T09:15:00Z,20.00,IDR,CREDIT,CASH,,",
    )

    first = _upload(client, headers, csv_text).json()
    second = _upload(client, headers, csv_text).json()

    assert (first["accepted"], first["quarantined"]) == (2, 0)
    assert (second["accepted"], second["quarantined"]) == (0, 2)
    assert all("already exists" in item["error_reason"] for item in second["quarantine_preview"])
    assert db.query(Transaction).count() == 2


def test_row_with_extra_fields_is_quarantined(client, db, auth_headers, account):
    csv_text = _csv("TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,note,surprise")

    body = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), csv_text).json()

    assert (body["accepted"], body["quarantined"]) == (0, 1)
    assert body["quarantine_preview"][0]["error_reason"] == "row has more fields than the header"
    assert db.query(QuarantineItem).one().raw_row["_extra_fields"] == ["surprise"]


def test_header_matching_ignores_case_whitespace_and_bom(client, auth_headers, account):
    header = " Transaction_Ref , ACCOUNT_NUMBER,transaction_date,amount,currency,direction,channel"
    content = "﻿" + header + "\nTXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CASH\n"

    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), content)

    assert response.status_code == 200
    assert response.json()["accepted"] == 1


def test_missing_required_column_rejects_whole_file(client, db, auth_headers, account):
    content = "transaction_ref,account_number,transaction_date,amount,currency,direction\nTXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT\n"

    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), content)

    assert response.status_code == 422
    assert response.json()["detail"] == "Missing required column(s): channel"
    assert db.query(Transaction).count() == 0
    assert db.query(QuarantineItem).count() == 0


def test_empty_file_rejected(client, auth_headers):
    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), "")

    assert response.status_code == 422


def test_non_utf8_file_rejected(client, auth_headers):
    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), b"\xff\xfe\x00\x00 not utf-8")

    assert response.status_code == 422


def test_file_with_nul_bytes_rejected(client, auth_headers):
    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), _csv("TXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CA\x00SH,,"))

    assert response.status_code == 422


def test_oversized_file_rejected(client, auth_headers):
    from app.routers.ingestion import MAX_UPLOAD_BYTES

    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), b"x" * (MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 413


def test_header_only_file_is_accepted_with_zero_rows(client, auth_headers):
    body = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), _csv()).json()

    assert (body["total_rows"], body["accepted"], body["quarantined"]) == (0, 0, 0)


def test_sample_file_matches_documented_outcome(client, db, auth_headers):
    seed_master_data(db)

    body = _upload(
        client,
        auth_headers(UserRole.ROLE_DATA_OPS),
        SAMPLE_FILE.read_bytes(),
        filename="transactions_sample.csv",
    ).json()

    assert (body["total_rows"], body["accepted"], body["quarantined"]) == (29, 22, 7)
    reasons = {item["row_number"]: item["error_reason"] for item in body["quarantine_preview"]}
    assert reasons[24].startswith("amount must be")
    assert reasons[25].startswith("transaction_date must be")
    assert reasons[26] == "account_number 'ACC-9999' does not exist"
    assert reasons[27].startswith("duplicate transaction_ref 'TXN-0001' within file")
    assert reasons[28] == "direction must be CREDIT or DEBIT"
    assert reasons[29] == "currency is required"
    assert reasons[30].startswith("amount must be")
