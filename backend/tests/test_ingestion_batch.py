"""FR-101 (batch registration) and FR-105 (per-batch processing log)."""

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.models.account import Account
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import EntityType, IngestionBatchStatus, UserRole
from app.models.ingestion_batch import IngestionBatch
from app.models.processing_log import (
    EVENT_BATCH_FAILED,
    EVENT_BATCH_REGISTERED,
    EVENT_RECONCILIATION_MISMATCH,
    EVENT_RECONCILIATION_OK,
    EVENT_VALIDATION_COMPLETED,
)
from app.models.quarantine_item import QuarantineItem
from app.models.transaction import Transaction
from app.services.audit import OBJECT_BATCH

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


def _csv(*rows: str) -> str:
    return "\n".join([HEADER, *rows]) + "\n"


def _upload(client, headers, content: str | bytes, filename: str = "upload.csv", **form):
    data = content.encode() if isinstance(content, str) else content
    return client.post(
        "/ingestion/transactions",
        headers=headers,
        files={"file": (filename, data, "text/csv")},
        data={key: str(value) for key, value in form.items()},
    )


def _log_codes(client, headers, batch_id) -> list[str]:
    detail = client.get(f"/ingestion/batches/{batch_id}", headers=headers).json()
    return [entry["event_code"] for entry in detail["processing_log"]]


def test_upload_registers_a_batch_with_checksum_and_counts(client, db, auth_headers, account):
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    csv_text = _csv(
        "TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,",
        "TXN-2,ACC-T1,2026-08-04,abc,IDR,CREDIT,CASH,,bad amount",
    )

    body = _upload(client, headers, csv_text, source_system="CORE_BANKING", business_date="2026-08-05").json()

    batch = db.query(IngestionBatch).one()
    assert batch.batch_ref == body["batch_ref"]
    assert batch.source_system == "CORE_BANKING"
    assert batch.business_date == date(2026, 8, 5)
    assert batch.file_checksum == hashlib.sha256(csv_text.encode()).hexdigest()
    assert batch.file_size_bytes == len(csv_text.encode())
    assert (batch.total_rows, batch.accepted_rows, batch.quarantined_rows) == (2, 1, 1)
    assert batch.status is IngestionBatchStatus.COMPLETED
    assert batch.completed_at is not None
    assert batch.registered_by is not None


def test_source_system_and_business_date_default_when_not_supplied(client, db, auth_headers, account):
    _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), _csv("TXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CASH,,"))

    batch = db.query(IngestionBatch).one()
    assert batch.source_system == "MANUAL_UPLOAD"
    assert batch.business_date == datetime.now(timezone.utc).date()


def test_rows_are_linked_to_the_batch_that_loaded_them(client, db, auth_headers, account):
    csv_text = _csv(
        "TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,",
        "TXN-2,ACC-T1,2026-08-04,abc,IDR,CREDIT,CASH,,bad amount",
    )

    _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), csv_text)

    batch = db.query(IngestionBatch).one()
    assert db.query(Transaction).one().ingestion_batch_id == batch.id
    assert db.query(QuarantineItem).one().ingestion_batch_id == batch.id


def test_same_file_twice_for_one_source_and_business_date_is_rejected(client, db, auth_headers, account):
    """FR-101 / TRD C-01: a duplicate checksum for the same source system and
    business date is refused, and nothing from the second attempt is stored."""
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    csv_text = _csv("TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,")

    first = _upload(client, headers, csv_text, business_date="2026-08-04")
    second = _upload(client, headers, csv_text, business_date="2026-08-04")

    assert first.status_code == 200
    assert second.status_code == 409
    assert first.json()["batch_ref"] in second.json()["detail"]
    assert db.query(IngestionBatch).count() == 1


def test_same_file_on_a_different_business_date_is_allowed(client, db, auth_headers, account):
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    csv_text = _csv("TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,")

    _upload(client, headers, csv_text, business_date="2026-08-04")
    second = _upload(client, headers, csv_text, business_date="2026-08-05")

    assert second.status_code == 200
    assert db.query(IngestionBatch).count() == 2


def test_same_file_from_a_different_source_system_is_allowed(client, db, auth_headers, account):
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    csv_text = _csv("TXN-1,ACC-T1,2026-08-03T09:15:00Z,10.00,IDR,CREDIT,CASH,,")

    _upload(client, headers, csv_text, source_system="CORE_BANKING", business_date="2026-08-04")
    second = _upload(client, headers, csv_text, source_system="WALLET", business_date="2026-08-04")

    assert second.status_code == 200
    assert db.query(IngestionBatch).count() == 2


def test_processing_log_records_registration_validation_and_reconciliation(client, db, auth_headers, account):
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    body = _upload(client, headers, _csv("TXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CASH,,")).json()

    batch = db.query(IngestionBatch).one()
    detail = client.get(f"/ingestion/batches/{batch.id}", headers=headers).json()

    assert detail["batch_ref"] == body["batch_ref"]
    # In the order they happened, not alphabetically.
    codes = [entry["event_code"] for entry in detail["processing_log"]]
    assert codes == [EVENT_BATCH_REGISTERED, EVENT_VALIDATION_COMPLETED, EVENT_RECONCILIATION_OK]
    validation = next(e for e in detail["processing_log"] if e["event_code"] == EVENT_VALIDATION_COMPLETED)
    assert validation["details"] == {"total_rows": 1, "accepted": 1, "quarantined": 0}


def test_declared_expected_records_mismatch_flags_the_batch_for_review(client, db, auth_headers, account):
    """TRD C-02: a reconciliation mismatch is surfaced, not quietly completed."""
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    csv_text = _csv("TXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CASH,,")

    body = _upload(client, headers, csv_text, expected_records=5).json()

    assert body["batch_status"] == IngestionBatchStatus.NEEDS_REVIEW.value
    batch = db.query(IngestionBatch).one()
    assert batch.status is IngestionBatchStatus.NEEDS_REVIEW
    assert EVENT_RECONCILIATION_MISMATCH in _log_codes(client, headers, batch.id)
    # The rows themselves still loaded — the flag is about the discrepancy.
    assert db.query(Transaction).count() == 1


def test_declared_expected_records_matching_completes_cleanly(client, db, auth_headers, account):
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    csv_text = _csv("TXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CASH,,")

    body = _upload(client, headers, csv_text, expected_records=1).json()

    assert body["batch_status"] == IngestionBatchStatus.COMPLETED.value


def test_structurally_invalid_file_leaves_a_failed_batch_with_zero_rows(client, db, auth_headers, account):
    """TRD C-01: a structural failure must stay visible as FAILED, not vanish."""
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    content = "transaction_ref,account_number,transaction_date,amount,currency,direction\nTXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT\n"

    response = _upload(client, headers, content)

    assert response.status_code == 422
    batch = db.query(IngestionBatch).one()
    assert batch.status is IngestionBatchStatus.FAILED
    assert (batch.total_rows, batch.accepted_rows, batch.quarantined_rows) == (0, 0, 0)
    assert batch.completed_at is not None
    assert db.query(Transaction).count() == 0
    assert _log_codes(client, headers, batch.id) == [EVENT_BATCH_REGISTERED, EVENT_BATCH_FAILED]


def test_non_utf8_file_leaves_a_failed_batch(client, db, auth_headers, account):
    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), b"\xff\xfe\x00\x00 not utf-8")

    assert response.status_code == 422
    assert db.query(IngestionBatch).one().status is IngestionBatchStatus.FAILED


def test_oversized_file_is_rejected_before_a_batch_is_registered(client, db, auth_headers):
    from app.routers.ingestion import MAX_UPLOAD_BYTES

    response = _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), b"x" * (MAX_UPLOAD_BYTES + 1))

    assert response.status_code == 413
    assert db.query(IngestionBatch).count() == 0


def test_batch_registration_and_completion_are_audited(client, db, auth_headers, account):
    """FR-1104: both ends of the batch lifecycle are audited, on one chain."""
    _upload(client, auth_headers(UserRole.ROLE_DATA_OPS), _csv("TXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CASH,,"))

    batch = db.query(IngestionBatch).one()
    entries = (
        db.query(AuditLog)
        .filter_by(object_type=OBJECT_BATCH)
        .order_by(AuditLog.created_at, AuditLog.to_state)
        .all()
    )

    assert [(e.from_state, e.to_state) for e in entries] == [
        (None, "REGISTERED"),
        ("REGISTERED", "COMPLETED"),
    ]
    assert {e.correlation_id for e in entries} == {batch.correlation_id}
    assert all(e.object_id == batch.id for e in entries)
    assert all(e.actor_role == UserRole.ROLE_DATA_OPS.value for e in entries)


def test_batch_list_is_newest_first_and_shows_who_registered_it(client, db, auth_headers, account):
    headers = auth_headers(UserRole.ROLE_DATA_OPS)
    _upload(client, headers, _csv("TXN-1,ACC-T1,2026-08-03,10.00,IDR,CREDIT,CASH,,"), business_date="2026-08-04")
    _upload(client, headers, _csv("TXN-2,ACC-T1,2026-08-03,20.00,IDR,CREDIT,CASH,,"), business_date="2026-08-05")

    body = client.get("/ingestion/batches", headers=headers).json()

    assert body["total"] == 2
    assert [item["business_date"] for item in body["items"]] == ["2026-08-05", "2026-08-04"]
    assert all(item["registered_by_email"] is not None for item in body["items"])


def test_batch_detail_404_for_unknown_id(client, auth_headers):
    response = client.get(
        "/ingestion/batches/00000000-0000-0000-0000-000000000000", headers=auth_headers(UserRole.ROLE_TRIAGE)
    )

    assert response.status_code == 404


@pytest.mark.parametrize("role", [UserRole.ROLE_ANALYST, UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR])
def test_every_role_can_read_batches_but_only_data_ops_can_upload(client, auth_headers, role, account):
    assert client.get("/ingestion/batches", headers=auth_headers(role)).status_code == 200
    assert _upload(client, auth_headers(role), _csv()).status_code == 403


def test_batch_endpoints_require_authentication(client):
    assert client.get("/ingestion/batches").status_code == 401
    assert client.get("/ingestion/batches/00000000-0000-0000-0000-000000000000").status_code == 401


def test_sample_file_batch_reconciles(client, db, auth_headers):
    from app.scripts.seed import seed_master_data

    seed_master_data(db)
    headers = auth_headers(UserRole.ROLE_DATA_OPS)

    body = _upload(
        client, headers, SAMPLE_FILE.read_bytes(), filename="transactions_sample.csv", expected_records=29
    ).json()

    assert body["batch_status"] == IngestionBatchStatus.COMPLETED.value
    batch = db.query(IngestionBatch).one()
    assert (batch.total_rows, batch.accepted_rows, batch.quarantined_rows) == (29, 22, 7)
    assert EVENT_RECONCILIATION_OK in _log_codes(client, headers, batch.id)
