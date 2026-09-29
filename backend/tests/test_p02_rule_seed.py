"""P02 moved from Python constants to rule RUL-0001 v1 (FR-301, FR-302).

The detector's behaviour is pinned by test_detection_p02.py, unchanged. These
tests pin what moved: the parameters now live in the database, the detector
reads them from there, and every alert names the exact version behind it.
"""

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select, text

from app.database import engine
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.enums import RuleEntityScope, RuleSeverity, RuleState, RuleWindowType, UserRole
from app.models.rule import Rule, RuleVersion
from app.scripts.seed import seed_master_data
from app.services.audit import OBJECT_DETECTION_RUN
from app.services.detection import p02_structuring
from app.services.detection.p02_structuring import TEMPLATE_VERSION, P02Parameters, run_p02_structuring
from app.services.ingestion import ingest_transactions_csv

SAMPLE_FILE = Path(__file__).resolve().parents[1] / "sample_data" / "transactions_sample.csv"

# TRD §8.6, the alert example that shows this rule.
TRD_P02_DESCRIPTION = (
    "Observes multiple transactions clustered below the reporting threshold within a rolling "
    "7-day window whose aggregate exceeds that threshold."
)


def _p02(db) -> tuple[Rule, RuleVersion]:
    return db.execute(
        select(Rule, RuleVersion).join(RuleVersion, RuleVersion.rule_id == Rule.id).where(Rule.rule_ref == "RUL-0001")
    ).one()


def _alerts_by_customer(db) -> dict[str, Alert]:
    rows = db.execute(select(Alert, Customer.customer_ref).join(Customer, Alert.customer_id == Customer.id)).all()
    return {customer_ref: alert for alert, customer_ref in rows}


def _replace_active_version(db, *, min_count: int) -> RuleVersion:
    """What FR-304 will do on approval, done directly because FR-304 is not
    built: retire v1 and make a v2 with a different parameter ACTIVE."""
    rule, v1 = _p02(db)
    v1.state = RuleState.RETIRED
    db.flush()
    v2 = RuleVersion(
        rule_id=rule.id,
        version=2,
        parent_version=1,
        template_version=v1.template_version,
        parameters={**v1.parameters, "min_count": min_count},
        window_type=v1.window_type,
        window_length=v1.window_length,
        entity_scope=v1.entity_scope,
        severity=v1.severity,
        reason_code=v1.reason_code,
        description=v1.description,
        state=RuleState.ACTIVE,
        change_summary=f"min_count 3 -> {min_count}",
    )
    db.add(v2)
    db.commit()
    return v2


@pytest.fixture()
def sample_data(db):
    seed_master_data(db)
    ingest_transactions_csv(db, file_name="transactions_sample.csv", content=SAMPLE_FILE.read_text())


# --- the seed ---------------------------------------------------------------------------


def test_p02_is_seeded_as_rul_0001_version_1_with_the_former_constants(db):
    rule, version = _p02(db)

    assert rule.pattern_code == "P02_STRUCTURING"
    assert (version.version, version.parent_version, version.state) == (1, None, RuleState.ACTIVE)
    assert version.template_version == TEMPLATE_VERSION == "p02_structuring_v1"
    # Exactly the values P02Parameters used to default to.
    assert version.parameters == {
        "reporting_threshold": "500000000.00",
        "band_lower_ratio": "0.70",
        "min_count": 3,
        "aggregate_multiple": "1.0",
        "currency": "IDR",
    }
    assert (version.window_type, version.window_length) == (RuleWindowType.ROLLING, timedelta(days=7))
    assert version.entity_scope == RuleEntityScope.CUSTOMER
    # FRD §8.2: "P02_STRUCTURING / RC-STRUCT-01", "Tingkat keparahan HIGH".
    assert (version.reason_code, version.severity) == ("RC-STRUCT-01", RuleSeverity.HIGH)
    assert version.description == TRD_P02_DESCRIPTION


def test_the_seed_says_it_bypassed_maker_checker(db):
    """No approver exists yet (FR-304, ROLE_MLRO), so the version says so itself."""
    rule, version = _p02(db)

    assert rule.created_by is None and version.changed_by is None
    assert "TEMPORARY BYPASS" in version.change_summary
    assert "maker-checker" in version.change_summary and "FR-304" in version.change_summary


def test_no_p02_parameter_values_remain_in_the_code():
    assert not hasattr(p02_structuring, "DEFAULT_PARAMETERS")
    with pytest.raises(TypeError):
        P02Parameters()  # no defaults: the values can only come from a rule version


def test_the_template_refuses_a_window_it_does_not_implement(db):
    _, version = _p02(db)
    db.expunge(version)

    version.window_type = RuleWindowType.CALENDAR
    with pytest.raises(ValueError, match="ROLLING windows of whole days"):
        P02Parameters.from_rule_version(version)

    version.window_type, version.window_length = RuleWindowType.ROLLING, timedelta(hours=36)
    with pytest.raises(ValueError, match="ROLLING windows of whole days"):
        P02Parameters.from_rule_version(version)


def test_seed_migration_round_trips_and_records_the_bypass_in_the_audit_trail(alembic_config):
    """Downgrade removes the seed and the alert link; upgrade restores them and
    writes the audit event that says the activation had no approver."""
    try:
        command.downgrade(alembic_config, "0005_rule_as_data")
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM rule")).scalar() == 0
            assert conn.execute(text("SELECT count(*) FROM rule_version")).scalar() == 0
            columns = conn.execute(
                text("SELECT column_name FROM information_schema.columns WHERE table_name = 'alert'")
            ).scalars().all()
            assert "rule_version_id" not in columns
    finally:
        command.upgrade(alembic_config, "head")
        engine.dispose()

    with engine.connect() as conn:
        assert conn.execute(text("SELECT rule_ref FROM rule")).scalars().all() == ["RUL-0001"]
        entry = conn.execute(
            text("SELECT object_type, from_state, to_state, actor_user_id, details FROM audit_log "
                 "WHERE action = 'RULE_SEEDED_ACTIVE'")
        ).one()
        # The next rule an analyst creates is RUL-0002, never a second RUL-0001.
        assert conn.execute(text("SELECT nextval('rule_ref_seq')")).scalar() >= 2
    assert (entry.object_type, entry.from_state, entry.to_state) == ("RULE_VERSION", None, "ACTIVE")
    assert entry.actor_user_id is None
    assert entry.details["maker_checker"] == "BYPASSED"
    assert entry.details["awaiting"] == ["FR-304", "ROLE_MLRO"]


# --- the detector reads the database ----------------------------------------------------


def test_every_alert_references_the_version_that_raised_it(db, sample_data):
    _, v1 = _p02(db)

    summary = run_p02_structuring(db)

    assert [(r.rule_ref, r.version) for r in summary.rules_evaluated] == [("RUL-0001", 1)]
    assert {(a.rule_ref, a.rule_version) for a in summary.alerts} == {("RUL-0001", 1)}
    assert {alert.rule_version_id for alert in _alerts_by_customer(db).values()} == {v1.id}


def test_the_detector_takes_its_parameters_from_the_active_version(db, sample_data):
    """Change min_count in the database only: the result follows. CUST-001 has
    4 in-band transactions, CUST-006 has 3."""
    v2 = _replace_active_version(db, min_count=4)

    summary = run_p02_structuring(db)

    assert [(r.rule_ref, r.version) for r in summary.rules_evaluated] == [("RUL-0001", 2)]
    alerts = _alerts_by_customer(db)
    assert set(alerts) == {"CUST-001"}
    assert alerts["CUST-001"].rule_version_id == v2.id
    assert alerts["CUST-001"].detection_details["MIN_COUNT_APPLIED"] == 4


def test_with_no_active_rule_the_run_evaluates_nothing_and_says_so(db, sample_data):
    _, v1 = _p02(db)
    v1.state = RuleState.RETIRED
    db.commit()

    summary = run_p02_structuring(db)

    assert (summary.rules_evaluated, summary.alerts_created, summary.alerts) == ([], 0, [])
    run_entry = db.query(AuditLog).filter_by(object_type=OBJECT_DETECTION_RUN).one()
    assert "no ACTIVE rule version" in run_entry.reason


def test_a_new_version_of_the_same_rule_does_not_re_raise_the_same_finding(db, sample_data):
    run_p02_structuring(db)
    _, v1 = _p02(db)
    _replace_active_version(db, min_count=3)  # identical parameters, new version

    second = run_p02_structuring(db)

    assert (second.alerts_created, second.alerts_already_existing) == (0, 2)
    assert db.query(Alert).count() == 2
    assert {alert.rule_version_id for alert in _alerts_by_customer(db).values()} == {v1.id}


def test_every_active_rule_for_the_pattern_is_evaluated(db, sample_data):
    """A second live P02 rule is a separate rule: its findings are its own alerts.
    Whether the API can ever create one depends on the reason-code registry
    (FRD §5.3: a reason code must be "unik dan terdaftar", decided in stage 3);
    the detector does not assume it cannot."""
    _, v1 = _p02(db)
    stricter = Rule(rule_ref="RUL-0002", pattern_code="P02_STRUCTURING", correlation_id=uuid.uuid4())
    db.add(stricter)
    db.flush()
    db.add(
        RuleVersion(
            rule_id=stricter.id,
            version=1,
            template_version=TEMPLATE_VERSION,
            parameters={**v1.parameters, "min_count": 4},
            window_type=RuleWindowType.ROLLING,
            window_length=timedelta(days=7),
            entity_scope=RuleEntityScope.CUSTOMER,
            severity=RuleSeverity.MEDIUM,
            reason_code="RC-STRUCT-02",
            description="Observes four or more transactions clustered below the reporting threshold.",
            state=RuleState.ACTIVE,
            change_summary="Initial draft",
        )
    )
    db.commit()

    summary = run_p02_structuring(db)

    assert [(r.rule_ref, r.version) for r in summary.rules_evaluated] == [("RUL-0001", 1), ("RUL-0002", 1)]
    assert sorted((a.rule_ref, a.customer_ref) for a in summary.alerts) == [
        ("RUL-0001", "CUST-001"),
        ("RUL-0001", "CUST-006"),
        ("RUL-0002", "CUST-001"),
    ]


# --- the alert shows its rule (FR-301 AC3, BR-301.2, FR-302 AC2) --------------------------


@pytest.mark.parametrize("role", list(UserRole))
def test_alert_detail_shows_the_rule_description_verbatim(client, auth_headers, sample_alerts, role):
    alert = sample_alerts["CUST-006"]

    body = client.get(f"/alerts/{alert.id}", headers=auth_headers(role)).json()

    assert body["rule"]["description"] == TRD_P02_DESCRIPTION
    assert body["rule"]["rule_ref"] == "RUL-0001"
    assert (body["rule"]["version"], body["rule"]["state"]) == (1, "ACTIVE")
    assert (body["rule"]["reason_code"], body["rule"]["severity"]) == ("RC-STRUCT-01", "HIGH")
    assert body["rule"]["parameters"]["min_count"] == 3
    assert (body["rule"]["window_type"], body["rule"]["window_length"]) == ("ROLLING", "P7D")


def test_an_alert_keeps_showing_its_own_version_after_the_rule_changes(client, db, auth_headers, sample_alerts):
    """FR-302 AC2, as far as it can be exercised before FR-304 exists: the
    alert from v1 still shows v1's parameters once v2 is the live version."""
    alert = sample_alerts["CUST-001"]
    _replace_active_version(db, min_count=4)

    body = client.get(f"/alerts/{alert.id}", headers=auth_headers(UserRole.ROLE_TRIAGE)).json()

    assert (body["rule"]["version"], body["rule"]["state"]) == (1, "RETIRED")
    assert body["rule"]["parameters"]["min_count"] == 3
    assert Decimal(body["detection_details"]["THRESHOLD_APPLIED"]) == Decimal("500000000.00")
