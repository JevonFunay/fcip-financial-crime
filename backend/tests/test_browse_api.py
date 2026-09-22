"""GET /transactions, GET /ingestion/quarantine and GET /overview — the read-only
endpoints behind the landing page."""

import pytest

from app.models.enums import UserRole

ALL_ROLES = list(UserRole)


# --- GET /transactions -----------------------------------------------------------------


def test_transactions_requires_authentication(client):
    assert client.get("/transactions").status_code == 401


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_role_can_browse_transactions(client, auth_headers, sample_alerts, role):
    response = client.get("/transactions", headers=auth_headers(role))

    assert response.status_code == 200
    assert response.json()["total"] == 22


def test_transaction_rows_carry_account_and_customer(client, auth_headers, sample_alerts):
    body = client.get("/transactions", params={"search": "TXN-0001"}, headers=auth_headers(UserRole.ROLE_ANALYST)).json()

    assert body["total"] == 1
    row = body["items"][0]
    assert row["transaction_ref"] == "TXN-0001"
    assert row["account_number"] == "ACC-1001"
    assert row["customer_ref"] == "CUST-001"
    assert row["customer_name"] == "Budi Santoso"
    assert row["amount"] == "450000000.00"
    assert row["direction"] == "CREDIT"
    assert row["channel"] == "CASH"


def test_transactions_sorted_newest_first(client, auth_headers, sample_alerts):
    items = client.get("/transactions", headers=auth_headers(UserRole.ROLE_ANALYST)).json()["items"]

    dates = [row["transaction_date"] for row in items]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.parametrize(
    ("term", "expected_ref"),
    [("ACC-6001", "CUST-006"), ("CUST-003", "CUST-003"), ("Dewi", "CUST-006")],
    ids=["by-account", "by-customer-ref", "by-customer-name"],
)
def test_search_matches_account_customer_ref_and_name(client, auth_headers, sample_alerts, term, expected_ref):
    body = client.get("/transactions", params={"search": term}, headers=auth_headers(UserRole.ROLE_ANALYST)).json()

    assert body["total"] > 0
    assert {row["customer_ref"] for row in body["items"]} == {expected_ref}


def test_search_is_case_insensitive(client, auth_headers, sample_alerts):
    body = client.get("/transactions", params={"search": "budi"}, headers=auth_headers(UserRole.ROLE_ANALYST)).json()

    assert body["total"] > 0
    assert all(row["customer_name"] == "Budi Santoso" for row in body["items"])


def test_search_with_no_match_returns_empty(client, auth_headers, sample_alerts):
    body = client.get("/transactions", params={"search": "zzzz"}, headers=auth_headers(UserRole.ROLE_ANALYST)).json()

    assert (body["total"], body["items"]) == (0, [])


def test_filter_by_direction(client, auth_headers, sample_alerts):
    headers = auth_headers(UserRole.ROLE_ANALYST)

    debits = client.get("/transactions", params={"direction": "DEBIT"}, headers=headers).json()
    credits = client.get("/transactions", params={"direction": "CREDIT"}, headers=headers).json()

    assert all(row["direction"] == "DEBIT" for row in debits["items"])
    assert debits["total"] + credits["total"] == 22


def test_invalid_direction_rejected(client, auth_headers):
    response = client.get("/transactions", params={"direction": "SIDEWAYS"}, headers=auth_headers(UserRole.ROLE_ANALYST))

    assert response.status_code == 422


def test_transactions_paginate(client, auth_headers, sample_alerts):
    headers = auth_headers(UserRole.ROLE_ANALYST)

    page1 = client.get("/transactions", params={"limit": 10, "offset": 0}, headers=headers).json()
    page2 = client.get("/transactions", params={"limit": 10, "offset": 10}, headers=headers).json()

    assert (page1["total"], len(page1["items"])) == (22, 10)
    assert len(page2["items"]) == 10
    assert {r["id"] for r in page1["items"]}.isdisjoint({r["id"] for r in page2["items"]})


# --- GET /ingestion/quarantine ----------------------------------------------------------


def test_quarantine_requires_authentication(client):
    assert client.get("/ingestion/quarantine").status_code == 401


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_role_can_read_quarantine(client, auth_headers, sample_alerts, role):
    response = client.get("/ingestion/quarantine", headers=auth_headers(role))

    assert response.status_code == 200
    assert response.json()["total"] == 7


def test_quarantine_rows_keep_raw_values_and_reason(client, auth_headers, sample_alerts):
    items = client.get("/ingestion/quarantine", headers=auth_headers(UserRole.ROLE_DATA_OPS)).json()["items"]

    bad_amount = next(item for item in items if item["row_number"] == 24)
    assert bad_amount["source_file_name"] == "transactions_sample.csv"
    assert bad_amount["raw_row"]["amount"] == "abc"
    assert bad_amount["error_reason"].startswith("amount must be")


def test_quarantine_is_empty_before_any_upload(client, auth_headers):
    body = client.get("/ingestion/quarantine", headers=auth_headers(UserRole.ROLE_DATA_OPS)).json()

    assert (body["total"], body["items"]) == (0, [])


# --- GET /overview ----------------------------------------------------------------------


def test_overview_requires_authentication(client):
    assert client.get("/overview").status_code == 401


def test_overview_counts_reflect_loaded_data(client, auth_headers, sample_alerts):
    body = client.get("/overview", headers=auth_headers(UserRole.ROLE_ANALYST)).json()

    assert body["customers"] == 6
    assert body["accounts"] == 8
    assert body["transactions"] == 22
    assert body["quarantined"] == 7
    assert body["alerts_open"] == 2
    assert body["alerts_escalated"] == 0
    assert body["alerts_disposed"] == 0
    assert body["cases"] == 0
    assert body["latest_transaction_date"].startswith("2026-08-")


def test_overview_tracks_alert_status_changes(client, auth_headers, sample_alerts):
    headers = auth_headers(UserRole.ROLE_TRIAGE)
    client.post(
        f"/alerts/{sample_alerts['CUST-001'].id}/disposition",
        headers=headers,
        json={"decision": "escalate", "reason": "Sub-threshold deposits across two accounts"},
    )

    body = client.get("/overview", headers=headers).json()

    assert (body["alerts_open"], body["alerts_escalated"]) == (1, 1)


def test_overview_on_empty_database(client, auth_headers):
    body = client.get("/overview", headers=auth_headers(UserRole.ROLE_ANALYST)).json()

    assert body["transactions"] == 0
    assert body["latest_transaction_date"] is None
