"""Tests for the Rule Management API (FR-301, FR-302, FR-303).

Covers:
- GET /rules         → list rules with optional pattern_code filter
- GET /rules/{ref}   → full rule detail with all versions
- GET /rules/{ref}/versions/{v} → single version detail
- POST /rules/{ref}/versions/{v}/simulate → dry-run, stores SimulationResult,
  advances DRAFT→IN_SIMULATION state, writes audit event
- Auth/RBAC checks
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.models.enums import RuleState, UserRole
from app.models.rule import Rule, RuleVersion, SimulationResult
from app.scripts.seed import seed_master_data
from app.services.audit import OBJECT_RULE_VERSION
from app.services.ingestion import ingest_transactions_csv
from data_helpers import TRANSACTIONS_CONTRACT as SAMPLE_FILE

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(client, auth_headers, role: UserRole) -> dict:
    return auth_headers(role)


# ---------------------------------------------------------------------------
# FR-301: list rules
# ---------------------------------------------------------------------------


class TestListRules:
    def test_unauthenticated_is_401(self, client: TestClient):
        r = client.get("/rules")
        assert r.status_code == 401

    def test_all_roles_can_list(self, client: TestClient, auth_headers):
        for role in UserRole:
            r = client.get("/rules", headers=auth_headers(role))
            assert r.status_code == 200, f"role {role} got {r.status_code}"

    def test_seeded_p02_rule_appears(self, client: TestClient, auth_headers):
        r = client.get("/rules", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        refs = [item["rule_ref"] for item in body["items"]]
        assert "RUL-0001" in refs

    def test_pattern_filter_returns_only_matching(self, client: TestClient, auth_headers):
        r = client.get("/rules?pattern_code=P02_STRUCTURING", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        for item in body["items"]:
            assert item["pattern_code"] == "P02_STRUCTURING"

    def test_pattern_filter_unknown_returns_empty(self, client: TestClient, auth_headers):
        r = client.get("/rules?pattern_code=P99_DOES_NOT_EXIST", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_pagination(self, client: TestClient, auth_headers):
        r = client.get("/rules?limit=1&offset=0", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) <= 1


# ---------------------------------------------------------------------------
# FR-301 / FR-302: get rule detail and single version
# ---------------------------------------------------------------------------


class TestGetRule:
    def test_get_seeded_rule(self, client: TestClient, auth_headers):
        r = client.get("/rules/RUL-0001", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 200
        body = r.json()
        assert body["rule_ref"] == "RUL-0001"
        assert body["pattern_code"] == "P02_STRUCTURING"
        # At least one version
        assert len(body["versions"]) >= 1
        v1 = body["versions"][-1]  # newest-first order, v1 is last when only one exists
        assert v1["version"] == 1
        assert v1["state"] == "ACTIVE"
        assert v1["reason_code"] == "RC-STRUCT-01"
        assert v1["severity"] == "HIGH"
        # Description must be non-empty (BR-301.2)
        assert len(v1["description"]) > 0
        # Parameters must be present and include the structuring threshold
        assert "reporting_threshold" in v1["parameters"]

    def test_rule_ref_is_case_insensitive(self, client: TestClient, auth_headers):
        r = client.get("/rules/rul-0001", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 200
        assert r.json()["rule_ref"] == "RUL-0001"

    def test_unknown_rule_is_404(self, client: TestClient, auth_headers):
        r = client.get("/rules/RUL-9999", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 404

    def test_get_single_version(self, client: TestClient, auth_headers):
        r = client.get("/rules/RUL-0001/versions/1", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 200
        body = r.json()
        assert body["version"] == 1
        assert body["template_version"] == "p02_structuring_v1"
        assert body["window_type"] == "ROLLING"
        # window_length is serialized as ISO-8601 duration (e.g. "P7D") or numeric seconds
        assert body["window_length"] is not None and len(str(body["window_length"])) > 0

    def test_unknown_version_is_404(self, client: TestClient, auth_headers):
        r = client.get("/rules/RUL-0001/versions/999", headers=auth_headers(UserRole.ROLE_ANALYST))
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# FR-303: simulate
# ---------------------------------------------------------------------------


class TestSimulate:
    """Simulation requires sample data to be loaded so the evaluate() call has
    something to scan.  The `with_data` fixture wires that up."""

    @pytest.fixture()
    def with_data(self, db: Session):
        seed_master_data(db)
        ingest_transactions_csv(db, file_name="transactions_sample.csv", content=SAMPLE_FILE.read_text())

    def test_simulate_requires_auth(self, client: TestClient):
        r = client.post("/rules/RUL-0001/versions/1/simulate", json={})
        assert r.status_code == 401

    def test_simulate_requires_data_ops_or_analyst(self, client: TestClient, auth_headers, with_data):
        # Roles that should be allowed
        for role in (UserRole.ROLE_DATA_OPS, UserRole.ROLE_ANALYST):
            # The seeded rule is ACTIVE; we need a DRAFT version for simulation.
            # We test RBAC against a non-existent rule to get a clean 404 (role
            # check happens before the DB lookup for these roles).
            r = client.post(
                "/rules/RUL-9999/versions/1/simulate",
                json={},
                headers=auth_headers(role),
            )
            # 404 = passed role check, hit DB — correct behaviour
            assert r.status_code == 404, f"{role} should reach DB lookup but got {r.status_code}"

        # Roles that must be rejected
        for role in (UserRole.ROLE_TRIAGE, UserRole.ROLE_INVESTIGATOR):
            r = client.post(
                "/rules/RUL-9999/versions/1/simulate",
                json={},
                headers=auth_headers(role),
            )
            assert r.status_code == 403, f"{role} should be forbidden but got {r.status_code}"

    def test_simulate_active_version_is_409(self, client: TestClient, auth_headers, with_data):
        """An ACTIVE version cannot be re-simulated — it should go through the
        normal detection run.  FR-303 only covers DRAFT/IN_SIMULATION."""
        r = client.post(
            "/rules/RUL-0001/versions/1/simulate",
            json={},
            headers=auth_headers(UserRole.ROLE_ANALYST),
        )
        assert r.status_code == 409
        assert "ACTIVE" in r.json()["detail"]

    def test_simulate_draft_version_returns_result(self, client: TestClient, auth_headers, db: Session, with_data):
        """End-to-end: create a DRAFT version, simulate it, check:
        - HTTP 201 with a valid SimulationResultOut body
        - SimulationResult row written to the DB
        - Version state advanced to IN_SIMULATION
        - Audit event written
        """
        # Insert a DRAFT version of RUL-0001 — same parameters as v1, just state=DRAFT.
        rule = db.scalar(select(Rule).where(Rule.rule_ref == "RUL-0001"))
        assert rule is not None
        v1 = db.scalar(select(RuleVersion).where(RuleVersion.rule_id == rule.id, RuleVersion.version == 1))
        assert v1 is not None

        draft = RuleVersion(
            rule_id=rule.id,
            version=2,
            parent_version=1,
            template_version=v1.template_version,
            parameters=v1.parameters,
            window_type=v1.window_type,
            window_length=v1.window_length,
            entity_scope=v1.entity_scope,
            severity=v1.severity,
            reason_code="RC-STRUCT-02-DRAFT",   # unique reason code (not ACTIVE)
            description="Draft version for simulation test",
            state=RuleState.DRAFT,
            change_summary="Test draft",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)

        r = client.post(
            "/rules/RUL-0001/versions/2/simulate",
            json={},
            headers=auth_headers(UserRole.ROLE_ANALYST),
        )
        assert r.status_code == 201, r.text
        body = r.json()

        # Body shape
        assert body["rule_ref"] == "RUL-0001"
        assert body["version"] == 2
        assert "transactions_scanned" in body
        assert "would_be_alert_count" in body
        assert "unique_entity_count" in body
        assert isinstance(body["sample_alerts"], list)
        assert isinstance(body["daily_distribution"], dict)

        # DB: SimulationResult row created
        sim = db.scalar(select(SimulationResult).where(SimulationResult.rule_version_id == draft.id))
        assert sim is not None
        assert sim.transactions_scanned == body["transactions_scanned"]

        # State advanced to IN_SIMULATION
        db.refresh(draft)
        assert draft.state == RuleState.IN_SIMULATION

        # Audit event written
        audit_row = db.scalar(
            select(AuditLog).where(
                AuditLog.object_type == OBJECT_RULE_VERSION,
                AuditLog.object_id == draft.id,
                AuditLog.to_state == RuleState.IN_SIMULATION.value,
            )
        )
        assert audit_row is not None
        assert audit_row.from_state == RuleState.DRAFT.value

    def test_simulate_with_period_filter(self, client: TestClient, auth_headers, db: Session, with_data):
        """A period that excludes all data returns zero scanned."""
        rule = db.scalar(select(Rule).where(Rule.rule_ref == "RUL-0001"))
        v1 = db.scalar(select(RuleVersion).where(RuleVersion.rule_id == rule.id, RuleVersion.version == 1))

        draft = RuleVersion(
            rule_id=rule.id,
            version=3,
            parent_version=1,
            template_version=v1.template_version,
            parameters=v1.parameters,
            window_type=v1.window_type,
            window_length=v1.window_length,
            entity_scope=v1.entity_scope,
            severity=v1.severity,
            reason_code="RC-STRUCT-03-DRAFT",
            description="Draft for period filter test",
            state=RuleState.DRAFT,
            change_summary="Period filter test draft",
        )
        db.add(draft)
        db.commit()

        # A period far in the future — no transactions there.
        r = client.post(
            "/rules/RUL-0001/versions/3/simulate",
            json={
                "period_start": "2099-01-01T00:00:00Z",
                "period_end": "2099-12-31T23:59:59Z",
            },
            headers=auth_headers(UserRole.ROLE_ANALYST),
        )
        assert r.status_code == 201
        body = r.json()
        assert body["transactions_scanned"] == 0
        assert body["would_be_alert_count"] == 0

    def test_simulate_period_start_after_end_is_422(self, client: TestClient, auth_headers, db: Session, with_data):
        rule = db.scalar(select(Rule).where(Rule.rule_ref == "RUL-0001"))
        v1 = db.scalar(select(RuleVersion).where(RuleVersion.rule_id == rule.id, RuleVersion.version == 1))

        draft = RuleVersion(
            rule_id=rule.id,
            version=4,
            parent_version=1,
            template_version=v1.template_version,
            parameters=v1.parameters,
            window_type=v1.window_type,
            window_length=v1.window_length,
            entity_scope=v1.entity_scope,
            severity=v1.severity,
            reason_code="RC-STRUCT-04-DRAFT",
            description="Draft for reversed period test",
            state=RuleState.DRAFT,
            change_summary="Reversed period test draft",
        )
        db.add(draft)
        db.commit()

        r = client.post(
            "/rules/RUL-0001/versions/4/simulate",
            json={
                "period_start": "2026-12-31T00:00:00Z",
                "period_end": "2026-01-01T00:00:00Z",
            },
            headers=auth_headers(UserRole.ROLE_ANALYST),
        )
        assert r.status_code == 422

    def test_second_simulate_on_in_simulation_version_succeeds(
        self, client: TestClient, auth_headers, db: Session, with_data
    ):
        """A version already IN_SIMULATION can be re-simulated (parameter
        tuning loop), but must not create a second audit event for the same
        state transition (it's already IN_SIMULATION)."""
        rule = db.scalar(select(Rule).where(Rule.rule_ref == "RUL-0001"))
        v1 = db.scalar(select(RuleVersion).where(RuleVersion.rule_id == rule.id, RuleVersion.version == 1))

        version = RuleVersion(
            rule_id=rule.id,
            version=5,
            parent_version=1,
            template_version=v1.template_version,
            parameters=v1.parameters,
            window_type=v1.window_type,
            window_length=v1.window_length,
            entity_scope=v1.entity_scope,
            severity=v1.severity,
            reason_code="RC-STRUCT-05-SIM",
            description="In-simulation version for re-simulate test",
            state=RuleState.IN_SIMULATION,
            change_summary="Re-simulate test",
        )
        db.add(version)
        db.commit()

        r = client.post(
            "/rules/RUL-0001/versions/5/simulate",
            json={},
            headers=auth_headers(UserRole.ROLE_ANALYST),
        )
        assert r.status_code == 201

        # State must still be IN_SIMULATION (no double-transition)
        db.refresh(version)
        assert version.state == RuleState.IN_SIMULATION

        # No audit event should have been written for this re-simulation
        # (no state change happened)
        audit_rows = db.scalars(
            select(AuditLog).where(
                AuditLog.object_type == OBJECT_RULE_VERSION,
                AuditLog.object_id == version.id,
            )
        ).all()
        assert len(audit_rows) == 0
