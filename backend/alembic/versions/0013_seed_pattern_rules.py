"""seed the FRD §8 pattern rules P01 and P03-P10 (ACTIVE, maker-checker bypassed)

Revision ID: 0013_seed_pattern_rules
Revises: 0012_lab_file_format
Create Date: 2026-11-03

Same arrangement as migration 0006 for P02: each pattern's parameters are the
first version of a rule, and the values are the defaults the FRD (§8.1-§8.10)
proposes for the synthetic dataset. They are not decisions: MQ-01 and MQ-02 are
still open with the mentor, and FRD §8.0 says every value is configurable,
versioned and approved. Change one by creating a new rule version, not by editing
code.

TEMPORARY BYPASS OF MAKER-CHECKER, stated in each version's change_summary and in
an audit event: a rule should reach ACTIVE through FR-304 (analyst submits, MLRO
approves), which is not built. Replace these with real approvals once it is.

P08's country list is the team's own synthetic construct (the same five codes the
data generator treats as high-risk); it is not an official designation of any
country. P10's ticket-band midpoints are team assumptions for the four declared
bands of the source contract. P11 and P12 are not seeded: P11 needs the
relationship graph and P12 the screening engine.
"""
import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_seed_pattern_rules"
down_revision: Union[str, None] = "0012_lab_file_format"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NS = uuid.UUID("6f1c2f0e-52a1-4b8e-9d3a-0d0f6a7b9c10")
CURRENCY = "IDR"

# pattern_code, template_version, scope, severity, reason_code, window_type, window_length, parameters, description
PATTERNS = [
    ("P01_LARGE_TXN", "p01_large_txn_v1", "CUSTOMER", "HIGH", "RC-LARGE-01", "ROLLING", "90 days",
     {"abs_threshold_individual": "100000000.00", "abs_threshold_merchant": "500000000.00",
      "relative_multiple": "5", "min_history_txns": 20, "currency": CURRENCY},
     "Observes a single transaction whose value is at or above the absolute threshold for the entity type, "
     "or above a multiple of the subject's own 90-day 95th percentile."),
    ("P03_RAPID_MOVEMENT", "p03_rapid_movement_v1", "ACCOUNT", "HIGH", "RC-RAPID-01", "ROLLING", "24 hours",
     {"passthrough_ratio": "0.80", "min_inbound_amount": "50000000.00", "min_outbound_count": 1, "currency": CURRENCY},
     "Observes an incoming credit followed within 24 hours by outgoing debits that together return at least "
     "the configured share of it, leaving a small residual balance."),
    ("P04_HIGH_VELOCITY", "p04_high_velocity_v1", "CUSTOMER", "MEDIUM", "RC-VELOCITY-01", "ROLLING", "24 hours",
     {"baseline_days": 90, "min_count_floor": 15, "deviation_sigma": "3", "alt_multiple": "4",
      "min_baseline_days": 30, "currency": CURRENCY},
     "Observes a number of transactions in a rolling 24 hours that is above an absolute floor and far above "
     "the subject's own daily baseline over the previous 90 days."),
    ("P05_DORMANT_REACTIVATION", "p05_dormant_reactivation_v1", "ACCOUNT", "MEDIUM", "RC-DORMANT-01", "ROLLING",
     "7 days",
     {"dormancy_days": 90, "min_reactivation_amount": "25000000.00", "min_reactivation_count": 5,
      "high_severity_aggregate": "100000000.00", "currency": CURRENCY},
     "Observes an account with no transactions for at least 90 days that then moves a material amount or "
     "number of transactions within 7 days."),
    ("P06_ROUND_AMOUNTS", "p06_round_amounts_v1", "CUSTOMER", "LOW", "RC-ROUND-01", "ROLLING", "30 days",
     {"rounding_unit": "1000000.00", "min_amount": "5000000.00", "min_count": 5, "min_proportion": "0.60",
      "currency": CURRENCY},
     "Observes a concentration of transactions whose values are exact multiples of the rounding unit, with "
     "both a minimum count and a minimum share of the subject's qualifying transactions."),
    ("P07_SUDDEN_SPIKE", "p07_sudden_spike_v1", "CUSTOMER", "MEDIUM", "RC-SPIKE-01", "CALENDAR", "7 days",
     {"baseline_weeks": 12, "min_absolute_increase": "100000000.00", "spike_multiple": "4",
      "min_baseline_weeks": 6, "high_severity_multiple": "10", "max_evidence_transactions": 20,
      "currency": CURRENCY},
     "Observes a calendar week whose total value is far above the subject's average of the previous 12 weeks, "
     "by both an absolute amount and a multiple."),
    ("P08_HIGH_RISK_GEO", "p08_high_risk_geo_v1", "CUSTOMER", "MEDIUM", "RC-GEO-01", "ROLLING", "30 days",
     {"countries": {"KY": "1.0", "PA": "1.0", "SC": "1.0", "VU": "1.0", "BZ": "1.0"}, "list_version": "geo_list_v1",
      "min_count": 3, "min_aggregate": "50000000.00", "min_exposure_share": "0.25", "currency": CURRENCY},
     "Observes transactions with counterparties in countries on the organisation's synthetic high-risk list "
     "when their count, value or share of the subject's value reaches the configured minimum."),
    ("P09_SHARED_DEVICE", "p09_shared_device_v1", "DEVICE", "MEDIUM", "RC-DEVICE-01", "ROLLING", "30 days",
     {"min_distinct_entities": 4, "min_distinct_accounts": 6, "currency": CURRENCY},
     "Observes a single device used to transact for an unusual number of distinct entities or accounts "
     "within 30 days."),
    ("P10_UNUSUAL_MERCHANT", "p10_unusual_merchant_v1", "MERCHANT", "MEDIUM", "RC-MERCHANT-01", "ROLLING", "30 days",
     {"ticket_deviation_multiple": "3",
      "ticket_band_midpoints": {"LT_50K": "25000", "50K_250K": "150000", "250K_1M": "625000", "GT_1M": "2000000"},
      "refund_rate": "0.15", "payer_concentration": "0.70", "top_n_payers": 5, "off_hours_share": "0.60",
      "min_txn_count": 30, "currency": CURRENCY},
     "Observes a merchant whose average ticket, reversal rate, payer concentration or out-of-hours share "
     "departs from its declared profile, and reports which condition applied."),
]

BYPASS_NOTE = (
    "Seeded by migration 0013 with the defaults proposed in FRD §8; awaiting mentor confirmation (MQ-01, MQ-02). "
    "TEMPORARY BYPASS: written directly as ACTIVE without maker-checker, because FR-304 is not built yet."
)


def _ids(pattern_code: str) -> tuple[str, str, str]:
    return tuple(str(uuid.uuid5(_NS, f"{pattern_code}:{part}")) for part in ("rule", "version", "correlation"))


def _next_free_ref(bind, floor: int) -> str:
    """RUL-0003, RUL-0004, ... so a fresh database always gets the same references; a number already
    taken (a rule created by hand) is skipped, never reused."""
    number = floor
    while bind.execute(sa.text("SELECT 1 FROM rule WHERE rule_ref = :ref"), {"ref": f"RUL-{number:04d}"}).first():
        number += 1
    return f"RUL-{number:04d}"


def upgrade() -> None:
    bind = op.get_bind()
    ref_floor = 3  # RUL-0001 is P02, RUL-0002 the ML_SCORE rule
    for code, template, scope, severity, reason, wtype, wlen, params, description in PATTERNS:
        rule_id, version_id, correlation_id = _ids(code)
        ref = _next_free_ref(bind, ref_floor)
        ref_floor = int(ref[4:]) + 1
        bind.execute(
            sa.text("INSERT INTO rule (id, rule_ref, pattern_code, correlation_id, created_by) "
                    "VALUES (:id, :ref, :pattern, :correlation, NULL)"),
            {"id": rule_id, "ref": ref, "pattern": code, "correlation": correlation_id},
        )
        bind.execute(
            sa.text(
                "INSERT INTO rule_version (id, rule_id, version, parent_version, template_version, parameters, "
                "window_type, window_length, entity_scope, severity, reason_code, description, state, "
                "change_summary, changed_by) VALUES (:id, :rule_id, 1, NULL, :template, CAST(:parameters AS jsonb), "
                "CAST(:wtype AS rule_window_type), CAST(:wlen AS interval), CAST(:scope AS rule_entity_scope), "
                "CAST(:severity AS rule_severity), :reason, :description, 'ACTIVE', :note, NULL)"
            ),
            {"id": version_id, "rule_id": rule_id, "template": template, "parameters": json.dumps(params),
             "wtype": wtype, "wlen": wlen, "scope": scope, "severity": severity, "reason": reason,
             "description": description, "note": BYPASS_NOTE},
        )
        bind.execute(
            sa.text(
                "INSERT INTO audit_log (correlation_id, actor_user_id, actor_role, object_type, object_id, "
                "from_state, to_state, reason, action, details) VALUES (:correlation, NULL, NULL, 'RULE_VERSION', "
                ":object_id, NULL, 'ACTIVE', :reason, 'RULE_SEEDED_ACTIVE', CAST(:details AS jsonb))"
            ),
            {"correlation": correlation_id, "object_id": version_id, "reason": BYPASS_NOTE,
             "details": json.dumps({"rule_ref": ref, "pattern_code": code, "version": 1, "maker_checker": "BYPASSED",
                                    "awaiting": ["FR-304", "MQ-01/MQ-02"], "parameters": params})},
        )


    # Only ever moves the sequence forward, so a later rule never reuses a seeded number.
    bind.execute(sa.text(
        "SELECT setval('rule_ref_seq', GREATEST((SELECT last_value FROM rule_ref_seq), :last))"), {"last": ref_floor - 1})


def downgrade() -> None:
    bind = op.get_bind()
    triggers = (("simulation_result", "simulation_result_append_only"), ("rule_version", "rule_version_guard"),
                ("rule", "rule_append_only"))
    for table, trigger in triggers:
        bind.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
    for code, *_ in PATTERNS:
        rule_id, version_id, _ = _ids(code)
        # Alerts raised by these rules reference their version, so they go with the rule's seed only if none exist.
        bind.execute(sa.text("DELETE FROM simulation_result WHERE rule_version_id = :v"), {"v": version_id})
        bind.execute(sa.text("DELETE FROM rule_version WHERE rule_id = :r"), {"r": rule_id})
        bind.execute(sa.text("DELETE FROM rule WHERE id = :r"), {"r": rule_id})
        bind.execute(sa.text("DELETE FROM audit_log WHERE action = 'RULE_SEEDED_ACTIVE' AND object_id = :v"),
                     {"v": version_id})
    for table, trigger in triggers:
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
