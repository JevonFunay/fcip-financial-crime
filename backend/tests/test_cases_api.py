import re
import uuid

import pytest

from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.enums import CaseStatus, UserRole

ESCALATION_REASON = "Multiple sub-threshold cash deposits across two accounts in 6 days."
CASE_NUMBER = re.compile(r"^CASE-\d{6}$")


def _escalate(client, auth_headers, alert):
    response = client.post(
        f"/alerts/{alert.id}/disposition",
        headers=auth_headers(UserRole.ROLE_TRIAGE),
        json={"decision": "escalate", "reason": ESCALATION_REASON},
    )
    assert response.status_code == 200


@pytest.fixture()
def escalated_alert(client, auth_headers, sample_alerts):
    alert = sample_alerts["CUST-001"]
    _escalate(client, auth_headers, alert)
    return alert


# --- POST /cases ------------------------------------------------------------------------


def test_create_requires_authentication(client):
    assert client.post("/cases", json={"alert_id": str(uuid.uuid4())}).status_code == 401


@pytest.mark.parametrize("role", [UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST])
def test_data_ops_and_analyst_cannot_open_cases(client, db, auth_headers, escalated_alert, role):
    response = client.post("/cases", headers=auth_headers(role), json={"alert_id": str(escalated_alert.id)})

    assert response.status_code == 403
    assert db.query(Case).count() == 0


@pytest.mark.parametrize("role", [UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR])
def test_triage_and_investigator_can_open_a_case_from_an_escalated_alert(client, db, auth_headers, escalated_alert, role):
    response = client.post("/cases", headers=auth_headers(role), json={"alert_id": str(escalated_alert.id)})

    assert response.status_code == 201
    body = response.json()
    assert CASE_NUMBER.match(body["case_number"])
    assert body["status"] == "OPEN"
    assert body["assigned_to"] is None
    assert body["opened_by_email"].startswith(role.value.lower())
    assert body["correlation_id"] == str(escalated_alert.correlation_id)
    assert [a["id"] for a in body["alerts"]] == [str(escalated_alert.id)]
    assert body["alerts"][0]["status"] == "ESCALATED"
    assert body["alerts"][0]["case_id"] == body["id"]

    db.refresh(escalated_alert)
    assert str(escalated_alert.case_id) == body["id"]
    case = db.get(Case, uuid.UUID(body["id"]))
    assert case.status is CaseStatus.OPEN
    assert case.opened_by is not None


def test_case_creation_extends_the_alert_audit_chain(client, db, auth_headers, escalated_alert):
    body = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_INVESTIGATOR), json={"alert_id": str(escalated_alert.id)}
    ).json()

    chain = (
        db.query(AuditLog)
        .filter(AuditLog.correlation_id == escalated_alert.correlation_id)
        .order_by(AuditLog.created_at, AuditLog.to_state)
        .all()
    )
    assert [(e.object_type, e.from_state, e.to_state) for e in chain] == [
        ("ALERT", None, "OPEN"),
        ("ALERT", "OPEN", "ESCALATED"),
        ("CASE", None, "OPEN"),
    ]
    case_entry = chain[-1]
    assert str(case_entry.object_id) == body["id"]
    assert case_entry.actor_role == "ROLE_INVESTIGATOR"
    assert case_entry.reason == f"Opened from escalated alert {escalated_alert.id}"


def test_case_numbers_are_unique_and_increasing(client, auth_headers, sample_alerts):
    headers = auth_headers(UserRole.ROLE_TRIAGE)
    for alert in sample_alerts.values():
        _escalate(client, auth_headers, alert)

    numbers = [
        client.post("/cases", headers=headers, json={"alert_id": str(alert.id)}).json()["case_number"]
        for alert in sample_alerts.values()
    ]

    assert len(set(numbers)) == 2
    assert int(numbers[1][5:]) > int(numbers[0][5:])


@pytest.mark.parametrize("decision", ["false_positive", None], ids=["disposed", "still-open"])
def test_only_escalated_alerts_can_become_cases(client, db, auth_headers, sample_alerts, decision):
    alert = sample_alerts["CUST-006"]
    if decision:
        response = client.post(
            f"/alerts/{alert.id}/disposition",
            headers=auth_headers(UserRole.ROLE_TRIAGE),
            json={"decision": decision, "reason": ESCALATION_REASON},
        )
        assert response.status_code == 200

    response = client.post("/cases", headers=auth_headers(UserRole.ROLE_INVESTIGATOR), json={"alert_id": str(alert.id)})

    assert response.status_code == 409
    assert "must be ESCALATED" in response.json()["detail"]
    assert db.query(Case).count() == 0


def test_an_alert_cannot_be_turned_into_two_cases(client, db, auth_headers, escalated_alert):
    headers = auth_headers(UserRole.ROLE_INVESTIGATOR)
    first = client.post("/cases", headers=headers, json={"alert_id": str(escalated_alert.id)}).json()

    response = client.post("/cases", headers=headers, json={"alert_id": str(escalated_alert.id)})

    assert response.status_code == 409
    assert response.json()["detail"] == f"Alert is already linked to case {first['case_number']}"
    assert db.query(Case).count() == 1


def test_create_with_unknown_alert_is_404(client, auth_headers):
    response = client.post("/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(uuid.uuid4())})

    assert response.status_code == 404


def test_create_with_malformed_body_is_422(client, auth_headers):
    assert client.post("/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": "nope"}).status_code == 422
    assert client.post("/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={}).status_code == 422


# --- GET /cases/{id} ---------------------------------------------------------------------


@pytest.mark.parametrize("role", list(UserRole))
def test_every_role_can_read_a_case(client, auth_headers, escalated_alert, role):
    created = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(escalated_alert.id)}
    ).json()

    response = client.get(f"/cases/{created['id']}", headers=auth_headers(role))

    assert response.status_code == 200
    body = response.json()
    assert body["case_number"] == created["case_number"]
    assert [a["customer_ref"] for a in body["alerts"]] == ["CUST-001"]


def test_alert_detail_links_back_to_its_case(client, auth_headers, escalated_alert):
    created = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(escalated_alert.id)}
    ).json()

    alert_body = client.get(f"/alerts/{escalated_alert.id}", headers=auth_headers(UserRole.ROLE_ANALYST)).json()

    assert alert_body["case_id"] == created["id"]


def test_get_requires_authentication(client):
    assert client.get(f"/cases/{uuid.uuid4()}").status_code == 401


def test_get_unknown_case_is_404(client, auth_headers):
    assert client.get(f"/cases/{uuid.uuid4()}", headers=auth_headers(UserRole.ROLE_ANALYST)).status_code == 404
