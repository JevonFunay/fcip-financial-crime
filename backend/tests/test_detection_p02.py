import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.alert import Alert, AlertTransaction
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import AlertStatus, EntityType, TransactionDirection, UserRole
from app.models.transaction import Transaction
from app.scripts.seed import seed_master_data
from app.services.detection.p02_structuring import (
    DEFAULT_PARAMETERS,
    PATTERN_CODE,
    P02Parameters,
    find_clusters,
    run_p02_structuring,
)
from app.services.ingestion import ingest_transactions_csv

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "transactions_sample.csv"
DAY0 = datetime(2026, 8, 1, tzinfo=timezone.utc)
M = Decimal("1000000")


def _txn(day: float, amount_millions: int = 400) -> SimpleNamespace:
    return SimpleNamespace(transaction_date=DAY0 + timedelta(days=day), amount=amount_millions * M)


# --- find_clusters: pure window logic, no database ---------------------------------


def test_three_in_band_transactions_within_a_week_form_one_cluster():
    clusters = find_clusters([_txn(0), _txn(1), _txn(2)], DEFAULT_PARAMETERS)

    assert len(clusters) == 1
    assert len(clusters[0].transactions) == 3
    assert clusters[0].window_start == DAY0
    assert clusters[0].window_end == DAY0 + timedelta(days=7)


def test_fewer_than_min_count_does_not_cluster():
    assert find_clusters([_txn(0), _txn(1)], DEFAULT_PARAMETERS) == []


def test_transactions_spread_beyond_the_window_do_not_cluster():
    assert find_clusters([_txn(0), _txn(8), _txn(16)], DEFAULT_PARAMETERS) == []


def test_window_end_is_exclusive():
    assert find_clusters([_txn(0), _txn(1), _txn(7)], DEFAULT_PARAMETERS) == []
    assert len(find_clusters([_txn(0), _txn(1), _txn(6.99)], DEFAULT_PARAMETERS)) == 1


def test_window_can_start_at_a_later_transaction():
    clusters = find_clusters([_txn(0), _txn(6), _txn(7), _txn(8)], DEFAULT_PARAMETERS)

    assert len(clusters) == 1
    assert clusters[0].window_start == DAY0 + timedelta(days=6)
    assert [t.transaction_date for t in clusters[0].transactions] == [DAY0 + timedelta(days=d) for d in (6, 7, 8)]


def test_each_transaction_is_evidence_for_at_most_one_cluster():
    txns = [_txn(d) for d in (0, 1, 2, 5, 6, 8, 9)]

    clusters = find_clusters(txns, DEFAULT_PARAMETERS)

    assert [len(c.transactions) for c in clusters] == [5, 2] or [len(c.transactions) for c in clusters] == [5]
    all_dates = [t.transaction_date for c in clusters for t in c.transactions]
    assert len(all_dates) == len(set(all_dates))


def test_aggregate_minimum_matters_when_band_is_wide():
    params = P02Parameters(band_lower_ratio=Decimal("0.10"))

    assert find_clusters([_txn(0, 100), _txn(1, 100), _txn(2, 100)], params) == []
    assert len(find_clusters([_txn(0, 200), _txn(1, 200), _txn(2, 100)], params)) == 1


def test_parameters_derive_band_and_aggregate_minimum():
    assert DEFAULT_PARAMETERS.band_lower == Decimal("350000000.00")
    assert DEFAULT_PARAMETERS.aggregate_minimum == Decimal("500000000.00")
    assert DEFAULT_PARAMETERS.window == timedelta(days=7)


# --- run_p02_structuring against the synthetic sample file ----------------------------


@pytest.fixture()
def sample_data(db):
    seed_master_data(db)
    summary = ingest_transactions_csv(db, file_name="transactions_sample.csv", content=SAMPLE_FILE.read_text())
    assert summary.accepted == 22


def _alerts_by_customer(db) -> dict[str, Alert]:
    rows = db.execute(select(Alert, Customer.customer_ref).join(Customer, Alert.customer_id == Customer.id)).all()
    return {customer_ref: alert for alert, customer_ref in rows}


def _evidence_refs(db, alert: Alert) -> set[str]:
    rows = db.execute(
        select(Transaction.transaction_ref)
        .join(AlertTransaction, AlertTransaction.transaction_id == Transaction.id)
        .where(AlertTransaction.alert_id == alert.id)
    )
    return set(rows.scalars())


def test_sample_data_raises_exactly_the_hand_verifiable_alerts(db, sample_data):
    summary = run_p02_structuring(db)

    assert summary.pattern_code == PATTERN_CODE
    assert (summary.alerts_created, summary.alerts_already_existing) == (2, 0)

    alerts = _alerts_by_customer(db)
    assert set(alerts) == {"CUST-001", "CUST-006"}
    assert all(alert.status is AlertStatus.OPEN for alert in alerts.values())
    assert _evidence_refs(db, alerts["CUST-001"]) == {"TXN-0001", "TXN-0002", "TXN-0003", "TXN-0004"}
    assert _evidence_refs(db, alerts["CUST-006"]) == {"TXN-0020", "TXN-0021", "TXN-0022"}


def test_detection_details_contain_the_required_factors(db, sample_data):
    run_p02_structuring(db)

    details = _alerts_by_customer(db)["CUST-001"].detection_details
    assert details["TXN_COUNT_IN_BAND"] == 4
    assert details["AGGREGATE_AMOUNT"] == "1820000000.00"
    assert details["THRESHOLD_APPLIED"] == "500000000.00"
    assert details["BAND_LOWER_APPLIED"] == "350000000.00"
    assert 0 < details["AMOUNT_DISPERSION_CV"] < 0.1
    assert details["DISTINCT_ACCOUNTS"] == 2
    assert details["DISTINCT_COUNTERPARTIES"] == 1
    assert details["CHANNEL_MIX"] == {"CASH": 3, "TRANSFER": 1}
    assert details["window_start"] == "2026-08-03T09:15:00+00:00"
    assert details["window_end"] == "2026-08-10T09:15:00+00:00"
    assert details["TRANSACTION_REFS"] == ["TXN-0001", "TXN-0002", "TXN-0003", "TXN-0004"]
    assert uuid.UUID(details["DETECTION_RUN_ID"])


def test_identical_amounts_have_zero_dispersion(db, sample_data):
    run_p02_structuring(db)

    alert = _alerts_by_customer(db)["CUST-006"]
    assert alert.detection_details["AMOUNT_DISPERSION_CV"] == 0.0
    assert alert.detection_details["DISTINCT_COUNTERPARTIES"] == 0
    assert alert.detection_details["CHANNEL_MIX"] == {"CASH": 3}
    assert alert.detection_details["AGGREGATE_AMOUNT"] == "1050000000.00"
    assert "3 transactions" in alert.reason and "1,050,000,000" in alert.reason


def test_rerun_on_same_data_is_idempotent(db, sample_data):
    first = run_p02_structuring(db)
    second = run_p02_structuring(db)

    assert (second.alerts_created, second.alerts_already_existing) == (0, 2)
    assert {a.alert_id for a in second.alerts} == {a.alert_id for a in first.alerts}
    assert all(not a.created for a in second.alerts)
    assert db.query(Alert).count() == 2
    assert db.query(AuditLog).count() == 2


def test_stored_window_bounds_reproduce_the_evidence(db, sample_data):
    """FR-307: re-querying with the stored bounds and parameters yields the same evidence set."""
    run_p02_structuring(db)

    for alert in _alerts_by_customer(db).values():
        details = alert.detection_details
        reproduced = db.execute(
            select(Transaction.id)
            .join(Account, Transaction.account_id == Account.id)
            .where(
                Account.customer_id == alert.customer_id,
                Transaction.currency == details["CURRENCY"],
                Transaction.amount >= Decimal(details["BAND_LOWER_APPLIED"]),
                Transaction.amount < Decimal(details["THRESHOLD_APPLIED"]),
                Transaction.transaction_date >= datetime.fromisoformat(details["window_start"]),
                Transaction.transaction_date < datetime.fromisoformat(details["window_end"]),
            )
        ).scalars()
        evidence = db.execute(
            select(AlertTransaction.transaction_id).where(AlertTransaction.alert_id == alert.id)
        ).scalars()
        assert set(reproduced) == set(evidence)


def test_each_alert_gets_an_audit_entry_sharing_its_correlation_id(db, sample_data):
    run_p02_structuring(db)

    for alert in _alerts_by_customer(db).values():
        entry = db.query(AuditLog).filter(AuditLog.object_id == alert.id).one()
        assert entry.object_type == "ALERT"
        assert (entry.from_state, entry.to_state) == (None, "OPEN")
        assert entry.correlation_id == alert.correlation_id
        assert entry.actor_user_id is None and entry.actor_role is None
        assert entry.reason == alert.reason


def _customer_with_transactions(db, customer_ref: str, currency: str, amounts: list[str]) -> Customer:
    customer = Customer(customer_ref=customer_ref, full_name=customer_ref, entity_type=EntityType.INDIVIDUAL)
    db.add(customer)
    db.flush()
    account = Account(account_number=f"{customer_ref}-ACC", customer_id=customer.id, account_type="SAVINGS", currency=currency)
    db.add(account)
    db.flush()
    for i, amount in enumerate(amounts):
        db.add(
            Transaction(
                transaction_ref=f"{customer_ref}-{i}",
                account_id=account.id,
                transaction_date=DAY0 + timedelta(days=i),
                amount=Decimal(amount),
                currency=currency,
                direction=TransactionDirection.CREDIT,
                channel="CASH",
            )
        )
    db.commit()
    return customer


def test_non_idr_transactions_are_ignored(db):
    _customer_with_transactions(db, "CUST-USD", "USD", ["400000000", "400000000", "400000000"])
    _customer_with_transactions(db, "CUST-IDR", "IDR", ["400000000", "400000000", "400000000"])

    run_p02_structuring(db)

    assert set(_alerts_by_customer(db)) == {"CUST-IDR"}


def test_amounts_at_or_above_threshold_are_not_in_band(db):
    _customer_with_transactions(db, "CUST-AT", "IDR", ["500000000", "500000000", "500000000"])
    _customer_with_transactions(db, "CUST-EDGE", "IDR", ["499999999.99", "350000000.00", "350000000.00"])
    _customer_with_transactions(db, "CUST-LOW", "IDR", ["349999999.99", "400000000", "400000000"])

    run_p02_structuring(db)

    assert set(_alerts_by_customer(db)) == {"CUST-EDGE"}


def test_debits_count_the_same_as_credits(db):
    customer = _customer_with_transactions(db, "CUST-DR", "IDR", ["400000000", "400000000", "400000000"])
    for txn in db.query(Transaction).all():
        txn.direction = TransactionDirection.DEBIT
    db.commit()

    run_p02_structuring(db)

    assert set(_alerts_by_customer(db)) == {customer.customer_ref}


# --- POST /detection/p02/run ---------------------------------------------------------


def test_run_endpoint_requires_authentication(client):
    assert client.post("/detection/p02/run").status_code == 401


@pytest.mark.parametrize("role", [UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR])
def test_run_endpoint_forbidden_for_triage_and_investigator(client, auth_headers, role):
    assert client.post("/detection/p02/run", headers=auth_headers(role)).status_code == 403


@pytest.mark.parametrize("role", [UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST])
def test_run_endpoint_records_the_triggering_user_as_actor(client, db, auth_headers, sample_data, role):
    response = client.post("/detection/p02/run", headers=auth_headers(role))

    assert response.status_code == 200
    body = response.json()
    assert (body["alerts_created"], body["alerts_already_existing"]) == (2, 0)
    assert {a["customer_ref"] for a in body["alerts"]} == {"CUST-001", "CUST-006"}
    assert all(a["created"] for a in body["alerts"])

    entries = db.query(AuditLog).all()
    assert len(entries) == 2
    assert {entry.actor_role for entry in entries} == {role.value}
    assert all(entry.actor_user_id is not None for entry in entries)
