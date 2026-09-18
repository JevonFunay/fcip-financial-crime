import uuid

import pytest

from app.models.audit_log import AuditLog
from app.models.enums import AlertStatus, UserRole

VALID_REASON = "Pattern matches known payroll batching, confirmed with branch."
ALL_ROLES = list(UserRole)


def _dispose(client, headers, alert_id, decision="false_positive", reason=VALID_REASON):
    return client.post(f"/alerts/{alert_id}/disposition", headers=headers, json={"decision": decision, "reason": reason})


# --- GET /alerts ----------------------------------------------------------------------


def test_list_requires_authentication(client):
    assert client.get("/alerts").status_code == 401


@pytest.mark.parametrize("role", ALL_ROLES)
def test_every_role_can_list_alerts(client, auth_headers, sample_alerts, role):
    response = client.get("/alerts", headers=auth_headers(role))

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert {item["customer_ref"] for item in body["items"]} == {"CUST-001", "CUST-006"}


def test_list_items_carry_queue_fields(client, auth_headers, sample_alerts):
    body = client.get("/alerts", headers=auth_headers(UserRole.ROLE_TRIAGE)).json()

    item = next(i for i in body["items"] if i["customer_ref"] == "CUST-006")
    assert item["id"] == str(sample_alerts["CUST-006"].id)
    assert item["pattern_code"] == "P02_STRUCTURING"
    assert item["status"] == "OPEN"
    assert item["customer_name"] == "Dewi Lestari"
    assert item["reason"].startswith("P02 Structuring: 3 transactions")
    assert item["case_id"] is None
    assert item["created_at"]


def test_list_filters_by_status(client, db, auth_headers, sample_alerts):
    headers = auth_headers(UserRole.ROLE_TRIAGE)
    _dispose(client, headers, sample_alerts["CUST-001"].id, decision="escalate")

    open_body = client.get("/alerts", params={"status": "OPEN"}, headers=headers).json()
    escalated_body = client.get("/alerts", params={"status": "ESCALATED"}, headers=headers).json()
    disposed_body = client.get("/alerts", params={"status": "DISPOSED"}, headers=headers).json()

    assert [i["customer_ref"] for i in open_body["items"]] == ["CUST-006"]
    assert [i["customer_ref"] for i in escalated_body["items"]] == ["CUST-001"]
    assert (disposed_body["total"], disposed_body["items"]) == (0, [])


def test_list_rejects_unknown_status(client, auth_headers):
    assert client.get("/alerts", params={"status": "BOGUS"}, headers=auth_headers(UserRole.ROLE_ANALYST)).status_code == 422


def test_list_paginates(client, auth_headers, sample_alerts):
    headers = auth_headers(UserRole.ROLE_ANALYST)

    page1 = client.get("/alerts", params={"limit": 1, "offset": 0}, headers=headers).json()
    page2 = client.get("/alerts", params={"limit": 1, "offset": 1}, headers=headers).json()

    assert (page1["total"], page1["limit"], page1["offset"]) == (2, 1, 0)
    assert len(page1["items"]) == 1 and len(page2["items"]) == 1
    assert page1["items"][0]["id"] != page2["items"][0]["id"]
    assert client.get("/alerts", params={"limit": 0}, headers=headers).status_code == 422


# --- GET /alerts/{id} ------------------------------------------------------------------


def test_detail_includes_evidence_transactions_and_factors(client, auth_headers, sample_alerts):
    alert = sample_alerts["CUST-001"]

    response = client.get(f"/alerts/{alert.id}", headers=auth_headers(UserRole.ROLE_INVESTIGATOR))

    assert response.status_code == 200
    body = response.json()
    assert body["correlation_id"] == str(alert.correlation_id)
    assert body["detection_details"]["TXN_COUNT_IN_BAND"] == 4
    assert [t["transaction_ref"] for t in body["transactions"]] == ["TXN-0001", "TXN-0002", "TXN-0003", "TXN-0004"]
    assert {t["account_number"] for t in body["transactions"]} == {"ACC-1001", "ACC-1002"}
    assert body["transactions"][0]["amount"] == "450000000.00"
    assert body["transactions"][0]["direction"] == "CREDIT"
    assert body["transactions"][2]["counterparty_ref"] == "CP-7781"
    assert body["disposed_by"] is None and body["disposition_reason"] is None


def test_detail_unknown_alert_is_404(client, auth_headers):
    assert client.get(f"/alerts/{uuid.uuid4()}", headers=auth_headers(UserRole.ROLE_ANALYST)).status_code == 404


def test_detail_malformed_id_is_422(client, auth_headers):
    assert client.get("/alerts/not-a-uuid", headers=auth_headers(UserRole.ROLE_ANALYST)).status_code == 422


# --- POST /alerts/{id}/disposition -------------------------------------------------------


@pytest.mark.parametrize("role", [UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST, UserRole.ROLE_INVESTIGATOR])
def test_only_triage_can_dispose(client, db, auth_headers, sample_alerts, role):
    alert = sample_alerts["CUST-001"]

    assert _dispose(client, auth_headers(role), alert.id).status_code == 403
    db.refresh(alert)
    assert alert.status is AlertStatus.OPEN


def test_false_positive_marks_alert_disposed_and_writes_audit(client, db, auth_headers, sample_alerts):
    alert = sample_alerts["CUST-001"]
    headers = auth_headers(UserRole.ROLE_TRIAGE)

    response = _dispose(client, headers, alert.id, decision="false_positive")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "DISPOSED"
    assert body["disposition_reason"] == VALID_REASON
    assert body["disposed_at"] is not None
    assert body["disposed_by_email"].startswith("role_triage-")

    db.refresh(alert)
    assert alert.status is AlertStatus.DISPOSED
    entries = db.query(AuditLog).filter(AuditLog.object_id == alert.id).order_by(AuditLog.created_at).all()
    assert [(e.from_state, e.to_state) for e in entries] == [(None, "OPEN"), ("OPEN", "DISPOSED")]
    disposition = entries[-1]
    assert disposition.correlation_id == alert.correlation_id
    assert disposition.actor_user_id == alert.disposed_by
    assert disposition.actor_role == "ROLE_TRIAGE"
    assert disposition.reason == VALID_REASON


def test_escalate_marks_alert_escalated(client, db, auth_headers, sample_alerts):
    alert = sample_alerts["CUST-006"]

    body = _dispose(client, auth_headers(UserRole.ROLE_TRIAGE), alert.id, decision="escalate").json()

    assert body["status"] == "ESCALATED"
    assert body["case_id"] is None
    entry = db.query(AuditLog).filter(AuditLog.object_id == alert.id, AuditLog.to_state == "ESCALATED").one()
    assert entry.from_state == "OPEN"


@pytest.mark.parametrize(
    "reason",
    ["", "   ", "too short", "x" * 19, " " * 10 + "x" * 19 + " " * 10],
    ids=["empty", "whitespace", "short", "19-chars", "19-chars-padded"],
)
def test_reason_shorter_than_20_characters_is_rejected(client, db, auth_headers, sample_alerts, reason):
    alert = sample_alerts["CUST-001"]

    response = _dispose(client, auth_headers(UserRole.ROLE_TRIAGE), alert.id, reason=reason)

    assert response.status_code == 422
    assert "at least 20 characters" in response.text
    db.refresh(alert)
    assert alert.status is AlertStatus.OPEN
    assert db.query(AuditLog).filter(AuditLog.object_id == alert.id).count() == 1


def test_reason_of_exactly_20_characters_is_accepted_and_trimmed(client, auth_headers, sample_alerts):
    body = _dispose(client, auth_headers(UserRole.ROLE_TRIAGE), sample_alerts["CUST-001"].id, reason="  " + "y" * 20 + "  ").json()

    assert body["disposition_reason"] == "y" * 20


def test_missing_reason_is_rejected(client, auth_headers, sample_alerts):
    response = client.post(
        f"/alerts/{sample_alerts['CUST-001'].id}/disposition",
        headers=auth_headers(UserRole.ROLE_TRIAGE),
        json={"decision": "escalate"},
    )

    assert response.status_code == 422


def test_unknown_decision_is_rejected(client, auth_headers, sample_alerts):
    response = _dispose(client, auth_headers(UserRole.ROLE_TRIAGE), sample_alerts["CUST-001"].id, decision="ignore")

    assert response.status_code == 422


def test_alert_can_only_be_disposed_once(client, db, auth_headers, sample_alerts):
    alert = sample_alerts["CUST-001"]
    headers = auth_headers(UserRole.ROLE_TRIAGE)
    _dispose(client, headers, alert.id, decision="escalate")

    response = _dispose(client, headers, alert.id, decision="false_positive")

    assert response.status_code == 409
    assert response.json()["detail"] == "Alert is already ESCALATED"
    db.refresh(alert)
    assert alert.status is AlertStatus.ESCALATED
    assert db.query(AuditLog).filter(AuditLog.object_id == alert.id).count() == 2


def test_dispose_unknown_alert_is_404(client, auth_headers):
    assert _dispose(client, auth_headers(UserRole.ROLE_TRIAGE), uuid.uuid4()).status_code == 404
