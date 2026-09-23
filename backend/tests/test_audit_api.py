"""FR-1105: searching and exporting the audit trail.

NFR-13 is the point of these: any state change has to be findable from the
audit trail via a correlation ID or object ID, without database access.
"""

import csv
import io
from pathlib import Path

import pytest

from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.enums import UserRole
from app.services.audit import OBJECT_ALERT, OBJECT_AUDIT_EXPORT, OBJECT_CASE, OBJECT_DETECTION_RUN

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "transactions_sample.csv"
REASON = "Multiple sub-threshold cash deposits across two accounts"


@pytest.fixture()
def escalated_chain(client, db, auth_headers, sample_alerts):
    """Drives one alert all the way to a case, so its correlation chain has
    three links: raised -> escalated -> case opened."""
    alert = sample_alerts["CUST-006"]
    triage = auth_headers(UserRole.ROLE_TRIAGE)
    client.post(f"/alerts/{alert.id}/disposition", headers=triage, json={"decision": "escalate", "reason": REASON})
    case = client.post("/cases", headers=triage, json={"alert_id": str(alert.id)}).json()
    db.expire_all()
    return db.get(Alert, alert.id), case


def test_search_requires_authentication(client):
    assert client.get("/audit").status_code == 401
    assert client.get("/audit/export").status_code == 401


@pytest.mark.parametrize("role", list(UserRole))
def test_every_built_role_can_read_the_audit_trail(client, auth_headers, role):
    assert client.get("/audit", headers=auth_headers(role)).status_code == 200


def test_correlation_id_returns_the_whole_chain_in_order(client, auth_headers, escalated_chain):
    alert, case = escalated_chain

    body = client.get(
        "/audit", headers=auth_headers(UserRole.ROLE_INVESTIGATOR), params={"correlation_id": str(alert.correlation_id)}
    ).json()

    assert body["total"] == 3
    assert [(e["object_type"], e["from_state"], e["to_state"]) for e in body["items"]] == [
        (OBJECT_ALERT, None, "OPEN"),
        (OBJECT_ALERT, "OPEN", "ESCALATED"),
        (OBJECT_CASE, None, "OPEN"),
    ]
    assert body["items"][2]["object_id"] == case["id"]
    assert body["items"][1]["reason"] == REASON


def test_filter_by_object_id_returns_only_that_object(client, auth_headers, escalated_chain):
    alert, _ = escalated_chain

    body = client.get(
        "/audit", headers=auth_headers(UserRole.ROLE_TRIAGE), params={"object_id": str(alert.id)}
    ).json()

    assert body["total"] == 2
    assert {e["object_type"] for e in body["items"]} == {OBJECT_ALERT}


def test_filter_by_object_type_is_case_insensitive(client, auth_headers, escalated_chain):
    headers = auth_headers(UserRole.ROLE_TRIAGE)

    upper = client.get("/audit", headers=headers, params={"object_type": "CASE"}).json()
    lower = client.get("/audit", headers=headers, params={"object_type": "case"}).json()

    assert upper["total"] == lower["total"] == 1


def test_filter_by_actor_email_ignores_case(client, auth_headers, escalated_chain):
    headers = auth_headers(UserRole.ROLE_TRIAGE)
    everything = client.get("/audit", headers=headers).json()
    actor_email = next(e["actor_email"] for e in everything["items"] if e["actor_email"])

    body = client.get("/audit", headers=headers, params={"actor_email": actor_email.upper()}).json()

    assert body["total"] >= 1
    assert {e["actor_email"] for e in body["items"]} == {actor_email}


def test_unknown_correlation_id_returns_empty_rather_than_404(client, auth_headers, escalated_chain):
    body = client.get(
        "/audit",
        headers=auth_headers(UserRole.ROLE_TRIAGE),
        params={"correlation_id": "00000000-0000-0000-0000-000000000000"},
    ).json()

    assert (body["total"], body["items"]) == (0, [])


def test_detection_run_events_are_searchable(client, auth_headers, sample_alerts):
    body = client.get(
        "/audit", headers=auth_headers(UserRole.ROLE_ANALYST), params={"object_type": OBJECT_DETECTION_RUN}
    ).json()

    assert body["total"] == 1
    assert body["items"][0]["to_state"] == "COMPLETED"


def test_paging_reports_the_unpaged_total(client, auth_headers, escalated_chain):
    headers = auth_headers(UserRole.ROLE_TRIAGE)

    first = client.get("/audit", headers=headers, params={"limit": 1, "offset": 0}).json()
    second = client.get("/audit", headers=headers, params={"limit": 1, "offset": 1}).json()

    assert first["total"] == second["total"] > 1
    assert len(first["items"]) == len(second["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_export_returns_csv_of_the_filtered_chain(client, auth_headers, escalated_chain):
    alert, _ = escalated_chain

    response = client.get(
        "/audit/export",
        headers=auth_headers(UserRole.ROLE_INVESTIGATOR),
        params={"correlation_id": str(alert.correlation_id)},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "audit_trail.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert [row["to_state"] for row in rows] == ["OPEN", "ESCALATED", "OPEN"]
    assert rows[0]["correlation_id"] == str(alert.correlation_id)


def test_export_is_itself_audited_with_the_filters_used(client, db, auth_headers, escalated_chain):
    """FRD §4.1.10: reading the audit trail out is an audited action too."""
    alert, _ = escalated_chain

    client.get(
        "/audit/export",
        headers=auth_headers(UserRole.ROLE_INVESTIGATOR),
        params={"correlation_id": str(alert.correlation_id)},
    )

    entry = db.query(AuditLog).filter_by(object_type=OBJECT_AUDIT_EXPORT).one()
    assert entry.to_state == "EXPORTED"
    assert entry.actor_role == UserRole.ROLE_INVESTIGATOR.value
    assert "Exported 3 of 3 audit event(s)" in entry.reason
    assert str(alert.correlation_id) in entry.reason


def test_export_audit_entry_records_that_no_filter_was_applied(client, db, auth_headers, escalated_chain):
    client.get("/audit/export", headers=auth_headers(UserRole.ROLE_TRIAGE))

    entry = db.query(AuditLog).filter_by(object_type=OBJECT_AUDIT_EXPORT).one()
    assert "filters: none" in entry.reason
