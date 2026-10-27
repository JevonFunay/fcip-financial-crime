import io
import uuid

import pytest
from app.models.enums import CaseStatus, EvidenceType, UserRole
from app.models.evidence import Evidence


def _escalate_and_create_case(client, auth_headers, sample_alerts):
    alert = sample_alerts["CUST-001"]
    client.post(
        f"/alerts/{alert.id}/disposition",
        headers=auth_headers(UserRole.ROLE_TRIAGE),
        json={"decision": "escalate", "reason": "Multiple sub-threshold cash deposits across two accounts in 6 days."},
    )
    case_res = client.post(
        "/cases",
        headers=auth_headers(UserRole.ROLE_TRIAGE),
        json={"alert_id": str(alert.id)},
    )
    return case_res.json()


def test_upload_evidence_to_case(client, db, auth_headers, sample_alerts):
    case = _escalate_and_create_case(client, auth_headers, sample_alerts)
    case_id = case["id"]

    file_content = b"Bank statement showing high velocity cash inflows."
    files = {"file": ("bank_statement.pdf", io.BytesIO(file_content), "application/pdf")}

    response = client.post(
        f"/cases/{case_id}/evidence?evidence_type=DOCUMENT&description=Customer+bank+statement",
        headers=auth_headers(UserRole.ROLE_INVESTIGATOR),
        files=files,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["case_id"] == case_id
    assert body["file_name"] == "bank_statement.pdf"
    assert body["evidence_type"] == "DOCUMENT"
    assert body["file_size_bytes"] == len(file_content)
    assert body["file_hash"] is not None
    assert body["description"] == "Customer bank statement"

    # Verify query
    ev = db.get(Evidence, uuid.UUID(body["id"]))
    assert ev is not None
    assert ev.file_hash == body["file_hash"]


def test_list_evidence_for_case(client, auth_headers, sample_alerts):
    case = _escalate_and_create_case(client, auth_headers, sample_alerts)
    case_id = case["id"]

    # Initial empty list
    res = client.get(f"/cases/{case_id}/evidence", headers=auth_headers(UserRole.ROLE_ANALYST))
    assert res.status_code == 200
    assert res.json()["total"] == 0

    # Upload two items
    client.post(
        f"/cases/{case_id}/evidence?evidence_type=DOCUMENT",
        headers=auth_headers(UserRole.ROLE_INVESTIGATOR),
        files={"file": ("doc1.txt", io.BytesIO(b"first item"), "text/plain")},
    )
    client.post(
        f"/cases/{case_id}/evidence?evidence_type=NOTE",
        headers=auth_headers(UserRole.ROLE_INVESTIGATOR),
        files={"file": ("note.txt", io.BytesIO(b"second item"), "text/plain")},
    )

    res = client.get(f"/cases/{case_id}/evidence", headers=auth_headers(UserRole.ROLE_AUDITOR))
    assert res.status_code == 200
    assert res.json()["total"] == 2
    assert [item["file_name"] for item in res.json()["items"]] == ["doc1.txt", "note.txt"]


def test_cannot_upload_evidence_to_closed_case(client, auth_headers, sample_alerts):
    case = _escalate_and_create_case(client, auth_headers, sample_alerts)
    case_id = case["id"]

    # Close case
    client.post(f"/cases/{case_id}/start", headers=auth_headers(UserRole.ROLE_INVESTIGATOR))
    client.post(
        f"/cases/{case_id}/submit-closure",
        headers=auth_headers(UserRole.ROLE_INVESTIGATOR),
        json={"reason": "Investigation concluded, all transactions accounted for."},
    )
    client.post(f"/cases/{case_id}/approve-closure", headers=auth_headers(UserRole.ROLE_MLRO))

    # Try uploading evidence
    res = client.post(
        f"/cases/{case_id}/evidence",
        headers=auth_headers(UserRole.ROLE_INVESTIGATOR),
        files={"file": ("doc.txt", io.BytesIO(b"too late"), "text/plain")},
    )
    assert res.status_code == 409
    assert "CLOSED" in res.json()["detail"]


def test_upload_evidence_unauthenticated_is_401(client):
    res = client.post(f"/cases/{uuid.uuid4()}/evidence", files={"file": ("x.txt", io.BytesIO(b"x"), "text/plain")})
    assert res.status_code == 401
