"""Rules as data, database layer (FR-301 AC1/AC2, FR-302, BR-302.1, FR-303).

These go straight at the tables, not through an API, to prove the guarantees
hold in the database itself: no future code path can edit a version's content,
delete a version, or activate a second copy of a reason code.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.audit_log import AuditLog
from app.models.enums import RuleEntityScope, RuleSeverity, RuleState, RuleWindowType, UserRole
from app.models.rule import Rule, RuleVersion, SimulationResult
from app.models.user import User

_DEFAULT_PARENT = object()


def _rule(db, ref: str = "RUL-9001") -> Rule:
    rule = Rule(rule_ref=ref, pattern_code="P02_STRUCTURING", correlation_id=uuid.uuid4())
    db.add(rule)
    db.flush()
    return rule


def _version(db, rule: Rule, version: int = 1, *, parent=_DEFAULT_PARENT, **overrides) -> RuleVersion:
    fields = {
        "rule_id": rule.id,
        "version": version,
        "parent_version": (version - 1 if version > 1 else None) if parent is _DEFAULT_PARENT else parent,
        "template_version": "p02_structuring_v1",
        "parameters": {"reporting_threshold": "500000000.00", "min_count": 3},
        "window_type": RuleWindowType.ROLLING,
        "window_length": timedelta(days=7),
        "entity_scope": RuleEntityScope.CUSTOMER,
        "severity": RuleSeverity.HIGH,
        "reason_code": "RC-TEST-01",
        "description": "Observes transactions clustered just below the reporting threshold.",
        "state": RuleState.DRAFT,
        "change_summary": "Initial draft",
    }
    fields.update(overrides)
    row = RuleVersion(**fields)
    db.add(row)
    db.flush()
    return row


def _user(db) -> User:
    user = User(
        email=f"analyst-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="not-a-real-hash",
        full_name="Test Analyst",
        role=UserRole.ROLE_ANALYST,
    )
    db.add(user)
    db.flush()
    return user


def _simulation(db, version: RuleVersion, **overrides) -> SimulationResult:
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    fields = {
        "rule_version_id": version.id,
        "period_start": start,
        "period_end": start + timedelta(days=30),
        "transactions_scanned": 100,
        "would_be_alert_count": 2,
        "unique_entity_count": 2,
        "daily_distribution": {"2026-08-03": 2},
        "overlap_by_rule": [],
        "sample_alerts": [],
        "run_by": _user(db).id,
    }
    fields.update(overrides)
    row = SimulationResult(**fields)
    db.add(row)
    db.flush()
    return row


# --- vocabulary ---------------------------------------------------------------------


def test_all_seven_rule_states_exist_although_only_two_are_reachable_yet(db):
    """TRD §8.2: FR-304/305 must not need a schema migration to add their states."""
    states = db.execute(text("SELECT unnest(enum_range(NULL::rule_state))::text")).scalars().all()

    assert states == [s.value for s in RuleState]
    assert states == [
        "DRAFT", "IN_SIMULATION", "PENDING_APPROVAL", "ACTIVE", "SUSPENDED", "RETIRED", "REJECTED",
    ]


# --- FR-302 / BR-302.1: immutability, enforced by trigger ---------------------------


@pytest.mark.parametrize("state", [RuleState.DRAFT, RuleState.IN_SIMULATION, RuleState.ACTIVE])
def test_a_versions_content_can_never_be_edited_in_any_state(db, state):
    """A change to what a rule does is a new version (FR-302 flow 1), even for a draft."""
    version = _version(db, _rule(db), state=state)
    db.commit()

    version.parameters = {"reporting_threshold": "1.00", "min_count": 3}
    with pytest.raises(DBAPIError, match="RULE_VERSION_IMMUTABLE"):
        db.commit()
    db.rollback()

    version.description = "Something else entirely."
    with pytest.raises(DBAPIError, match="RULE_VERSION_IMMUTABLE"):
        db.commit()


def test_only_the_state_of_a_draft_changes(db):
    version = _version(db, _rule(db))
    db.commit()

    version.state = RuleState.IN_SIMULATION
    db.commit()

    db.refresh(version)
    assert version.state == RuleState.IN_SIMULATION


def test_an_active_version_cannot_go_back_to_draft(db):
    version = _version(db, _rule(db), state=RuleState.ACTIVE)
    db.commit()

    version.state = RuleState.DRAFT
    with pytest.raises(DBAPIError, match="only move to SUSPENDED or RETIRED"):
        db.commit()


@pytest.mark.parametrize("target", [RuleState.SUSPENDED, RuleState.RETIRED])
def test_an_active_version_can_be_suspended_or_retired(db, target):
    """TRD §7.3: the one permitted change to an ACTIVE row, needed by FR-305."""
    version = _version(db, _rule(db), state=RuleState.ACTIVE)
    db.commit()

    version.state = target
    db.commit()

    db.refresh(version)
    assert version.state == target


@pytest.mark.parametrize("state", [RuleState.DRAFT, RuleState.ACTIVE])
def test_rule_versions_are_never_deleted(db, state):
    version = _version(db, _rule(db), state=state)
    db.commit()

    with pytest.raises(DBAPIError, match="never deleted"):
        db.execute(text("DELETE FROM rule_version WHERE id = :id"), {"id": version.id})


# --- FR-301 AC2: an ACTIVE reason code is unique ------------------------------------


def test_two_active_rules_cannot_share_a_reason_code_in_any_case(db):
    _version(db, _rule(db, "RUL-9001"), state=RuleState.ACTIVE, reason_code="RC-TEST-DUP")
    db.commit()

    with pytest.raises(IntegrityError, match="uq_rule_version_active_reason_code"):
        _version(db, _rule(db, "RUL-9002"), state=RuleState.ACTIVE, reason_code="rc-test-dup")


def test_drafts_may_reuse_an_active_reason_code(db):
    """E1 is checked when a draft is created (API layer); the database only
    guarantees that two such versions are never ACTIVE together."""
    _version(db, _rule(db, "RUL-9001"), state=RuleState.ACTIVE, reason_code="RC-TEST-DUP")
    _version(db, _rule(db, "RUL-9002"), reason_code="RC-TEST-DUP")
    db.commit()


def test_a_rule_has_at_most_one_active_version(db):
    rule = _rule(db)
    _version(db, rule, 1, state=RuleState.ACTIVE)
    db.commit()

    with pytest.raises(IntegrityError, match="uq_rule_version_one_active_per_rule"):
        _version(db, rule, 2, state=RuleState.ACTIVE, reason_code="RC-TEST-02")


# --- lineage --------------------------------------------------------------------------


def test_version_numbers_are_unique_per_rule(db):
    rule = _rule(db)
    _version(db, rule, 1)
    db.commit()

    with pytest.raises(IntegrityError, match="uq_rule_version_rule_id_version"):
        _version(db, rule, 1)


def test_the_same_version_number_exists_independently_per_rule(db):
    _version(db, _rule(db, "RUL-9001"), 1)
    _version(db, _rule(db, "RUL-9002"), 1)
    db.commit()


@pytest.mark.parametrize(
    ("version", "parent", "constraint"),
    [
        (1, 1, "ck_rule_version_lineage"),  # v1 has no parent
        (2, None, "ck_rule_version_lineage"),  # every later version has one
        (2, 2, "ck_rule_version_lineage"),  # a parent is always earlier
        (3, 2, "fk_rule_version_parent"),  # and it must exist on the same rule
    ],
)
def test_lineage_is_consistent(db, version, parent, constraint):
    rule = _rule(db)
    _version(db, rule, 1)
    db.commit()

    with pytest.raises(IntegrityError, match=constraint):
        _version(db, rule, version, parent=parent)


def test_a_parent_must_belong_to_the_same_rule(db):
    other = _rule(db, "RUL-9001")
    _version(db, other, 1)
    _version(db, other, 2)
    rule = _rule(db, "RUL-9002")
    _version(db, rule, 1)
    db.commit()

    # v2 exists on the other rule, not on this one.
    with pytest.raises(IntegrityError, match="fk_rule_version_parent"):
        _version(db, rule, 3, parent=2)


# --- FR-301 AC1: the mandatory fields -------------------------------------------------


@pytest.mark.parametrize(
    ("field", "constraint"),
    [
        ("reason_code", "ck_rule_version_reason_code_present"),
        ("description", "ck_rule_version_description_present"),
        ("change_summary", "ck_rule_version_change_summary_present"),
    ],
)
def test_mandatory_text_cannot_be_blank(db, field, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        _version(db, _rule(db), **{field: "   "})


def test_severity_is_mandatory(db):
    with pytest.raises(IntegrityError, match="severity"):
        _version(db, _rule(db), severity=None)


def test_severity_vocabulary_is_the_frd_scale(db):
    """FRD §8.0: "Skala tingkat keparahan: CRITICAL, HIGH, MEDIUM, LOW". Stored
    low-to-high so that severities compare in order."""
    values = db.execute(text("SELECT unnest(enum_range(NULL::rule_severity))::text")).scalars().all()

    assert values == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def test_window_length_must_be_positive(db):
    with pytest.raises(IntegrityError, match="ck_rule_version_window_length_positive"):
        _version(db, _rule(db), window_length=timedelta(0))


def test_parameters_must_be_a_json_object(db):
    with pytest.raises(IntegrityError, match="ck_rule_version_parameters_object"):
        _version(db, _rule(db), parameters=[3, 7])


# --- append-only identity and simulation results ------------------------------------


def test_a_rules_identity_is_never_changed_or_deleted(db):
    rule = _rule(db)
    db.commit()

    rule.pattern_code = "P01_LARGE_TXN"
    with pytest.raises(DBAPIError, match="APPEND_ONLY"):
        db.commit()
    db.rollback()

    with pytest.raises(DBAPIError, match="APPEND_ONLY"):
        db.execute(text("DELETE FROM rule WHERE id = :id"), {"id": rule.id})


def test_simulation_results_are_never_changed_or_deleted(db):
    simulation = _simulation(db, _version(db, _rule(db)))
    db.commit()

    simulation.would_be_alert_count = 0
    simulation.unique_entity_count = 0
    with pytest.raises(DBAPIError, match="APPEND_ONLY"):
        db.commit()
    db.rollback()

    with pytest.raises(DBAPIError, match="APPEND_ONLY"):
        db.execute(text("DELETE FROM simulation_result WHERE id = :id"), {"id": simulation.id})


def test_a_zero_result_simulation_is_a_valid_row(db):
    """FR-303 E2: zero would-be alerts is a result, not a failure."""
    simulation = _simulation(db, _version(db, _rule(db)), would_be_alert_count=0, unique_entity_count=0,
                             daily_distribution={})
    db.commit()

    assert simulation.would_be_alert_count == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"period_end": datetime(2026, 8, 1, tzinfo=timezone.utc)},  # empty period
        {"would_be_alert_count": 1, "unique_entity_count": 2},  # more entities than alerts
        {"sample_alerts": [{}] * 21},  # FR-303: the sample is the top 20
        {"overlap_by_rule": {}},  # a list of rules, not a map
    ],
)
def test_simulation_result_shape_is_checked(db, overrides):
    with pytest.raises(IntegrityError, match="ck_simulation_result_"):
        _simulation(db, _version(db, _rule(db)), **overrides)


# --- audit_log: action + details ------------------------------------------------------


def test_audit_log_carries_an_action_and_structured_details(db):
    entry = AuditLog(
        correlation_id=uuid.uuid4(),
        object_type="RULE_VERSION",
        object_id=uuid.uuid4(),
        from_state=None,
        to_state="DRAFT",
        action="RULE_VERSION_CREATED",
        details={"diff": [{"field": "parameters.min_count", "from": 3, "to": 4}]},
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)

    assert entry.action == "RULE_VERSION_CREATED"
    assert entry.details["diff"][0]["to"] == 4
