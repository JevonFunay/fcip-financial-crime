import re
import uuid

import pytest

from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.enums import CaseStatus, UserRole
from app.models.user import User

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


# --- GET /cases (FR-808) -----------------------------------------------------------------


def test_list_cases_empty(client, auth_headers):
    res = client.get("/cases", headers=auth_headers(UserRole.ROLE_INVESTIGATOR))
    assert res.status_code == 200
    assert res.json()["total"] == 0
    assert res.json()["items"] == []


def test_list_cases_with_data(client, auth_headers, escalated_alert):
    created = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(escalated_alert.id)}
    ).json()

    res = client.get("/cases", headers=auth_headers(UserRole.ROLE_INVESTIGATOR))
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == created["id"]
    assert data["items"][0]["status"] == "OPEN"
    assert data["items"][0]["alert_count"] == 1


# --- Case Lifecycle & Maker-Checker (FR-804, FR-814-816) ---------------------------------


def test_case_lifecycle_start_investigation(client, auth_headers, escalated_alert):
    created = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(escalated_alert.id)}
    ).json()

    # Move to IN_PROGRESS
    res = client.post(f"/cases/{created['id']}/start", headers=auth_headers(UserRole.ROLE_INVESTIGATOR))
    assert res.status_code == 200
    assert res.json()["status"] == "IN_PROGRESS"


def test_maker_checker_closure_flow(client, auth_headers, escalated_alert):
    created = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(escalated_alert.id)}
    ).json()

    # 1. Start investigation
    client.post(f"/cases/{created['id']}/start", headers=auth_headers(UserRole.ROLE_INVESTIGATOR))

    # 2. Maker submits closure (needs >= 10 chars reason)
    maker_headers = auth_headers(UserRole.ROLE_INVESTIGATOR)
    sub_res = client.post(
        f"/cases/{created['id']}/submit-closure",
        headers=maker_headers,
        json={"reason": "Investigation completed: no illicit structuring verified upon source checking."},
    )
    assert sub_res.status_code == 200
    assert sub_res.json()["status"] == "PENDING_CLOSURE"

    # 3. Segregation of duties: Maker cannot approve their own closure
    self_approve = client.post(
        f"/cases/{created['id']}/approve-closure",
        headers=maker_headers,
    )
    assert self_approve.status_code in (403, 409)

    # 4. Checker (Senior Investigator or MLRO) approves closure
    checker_headers = auth_headers(UserRole.ROLE_MLRO)
    app_res = client.post(
        f"/cases/{created['id']}/approve-closure",
        headers=checker_headers,
    )
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "CLOSED"
    assert app_res.json()["closure_approved_by"] is not None


def test_checker_can_reject_closure(client, auth_headers, escalated_alert):
    created = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(escalated_alert.id)}
    ).json()

    maker_headers = auth_headers(UserRole.ROLE_INVESTIGATOR)
    client.post(
        f"/cases/{created['id']}/submit-closure",
        headers=maker_headers,
        json={"reason": "Requesting closure due to lack of evidence."},
    )

    checker_headers = auth_headers(UserRole.ROLE_SENIOR_INVESTIGATOR)
    rej_res = client.post(
        f"/cases/{created['id']}/reject-closure",
        headers=checker_headers,
        json={"reason": "Need additional transaction documentation from source account."},
    )
    assert rej_res.status_code == 200
    assert rej_res.json()["status"] == "IN_PROGRESS"
    assert rej_res.json()["closed_reason"] is None


def test_case_assignment(client, db, auth_headers, escalated_alert):
    created = client.post(
        "/cases", headers=auth_headers(UserRole.ROLE_TRIAGE), json={"alert_id": str(escalated_alert.id)}
    ).json()

    inv = User(
        email="assignee-inv@example.com",
        hashed_password="hash",
        full_name="Assignee Investigator",
        role=UserRole.ROLE_INVESTIGATOR,
    )
    db.add(inv)
    db.commit()

    res = client.post(
        f"/cases/{created['id']}/assign",
        headers=auth_headers(UserRole.ROLE_TRIAGE),
        json={"assignee_id": str(inv.id)},
    )
    assert res.status_code == 200
    assert res.json()["assigned_to"] == str(inv.id)
    assert res.json()["assigned_to_email"] == "assignee-inv@example.com"

