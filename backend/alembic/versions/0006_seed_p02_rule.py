"""seed P02 as rule RUL-0001 v1 and link every alert to its rule version

Revision ID: 0006_seed_p02_rule
Revises: 0005_rule_as_data
Create Date: 2026-09-29

P02's parameters stop being Python constants (the former P02Parameters
defaults) and become the first version of a rule. The values below are those
constants exactly; they never changed since the baseline commit c98df92, which
is also why every existing P02 alert can be attributed to this version.

TEMPORARY BYPASS OF MAKER-CHECKER. A rule should only reach ACTIVE through
FR-304 (analyst submits, MLRO approves). Neither FR-304 nor ROLE_MLRO exists
yet, so this seed writes the version straight as ACTIVE. The bypass is stated
in the version's change_summary and in an audit event, so it is visible
wherever the rule is. Replace it with a real approval once FR-304 is built.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006_seed_p02_rule"
down_revision: Union[str, None] = "0005_rule_as_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Fixed, so the seed is identical on every database it is applied to.
P02_RULE_ID = "a359ef99-431a-4fe0-9329-66284bceae12"
P02_V1_ID = "e4e1c672-9c81-493d-b454-c7c66f68c00a"
P02_CORRELATION_ID = "31e7275f-a53c-4b66-918e-a0123ced5b07"

P02_PARAMETERS = {
    "reporting_threshold": "500000000.00",
    "band_lower_ratio": "0.70",
    "min_count": 3,
    "aggregate_multiple": "1.0",
    # The threshold is in IDR and there is no FX conversion yet.
    "currency": "IDR",
}
# Reason code and severity are FRD §8.2's own for P02 ("P02_STRUCTURING /
# RC-STRUCT-01", "Tingkat keparahan HIGH"). The description is the sentence the
# TRD §8.6 alert example shows for this rule, an English rendering of FRD
# §8.2's defensive logic statement.
P02_REASON_CODE = "RC-STRUCT-01"
P02_SEVERITY = "HIGH"
P02_DESCRIPTION = (
    "Observes multiple transactions clustered below the reporting threshold within a rolling "
    "7-day window whose aggregate exceeds that threshold."
)
BYPASS_NOTE = (
    "Seeded by migration 0006 from the former P02Parameters constants, unchanged since c98df92. "
    "TEMPORARY BYPASS: written directly as ACTIVE without maker-checker, because FR-304 and "
    "ROLE_MLRO are not built yet."
)


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO rule (id, rule_ref, pattern_code, correlation_id, created_by) "
            "VALUES (:id, 'RUL-0001', 'P02_STRUCTURING', :correlation_id, NULL)"
        ),
        {"id": P02_RULE_ID, "correlation_id": P02_CORRELATION_ID},
    )
    # RUL-0001 is taken, so the next rule is RUL-0002. Only ever moves the
    # sequence forward, so re-applying after a downgrade cannot reissue a
    # number that a later rule already holds.
    bind.execute(sa.text("SELECT setval('rule_ref_seq', GREATEST(1, (SELECT last_value FROM rule_ref_seq)))"))

    bind.execute(
        sa.text(
            "INSERT INTO rule_version (id, rule_id, version, parent_version, template_version, parameters, "
            "window_type, window_length, entity_scope, severity, reason_code, description, state, "
            "change_summary, changed_by) "
            "VALUES (:id, :rule_id, 1, NULL, 'p02_structuring_v1', CAST(:parameters AS jsonb), "
            "'ROLLING', interval '7 days', 'CUSTOMER', :severity, :reason_code, :description, 'ACTIVE', "
            ":change_summary, NULL)"
        ),
        {
            "id": P02_V1_ID,
            "rule_id": P02_RULE_ID,
            "parameters": json.dumps(P02_PARAMETERS),
            "severity": P02_SEVERITY,
            "reason_code": P02_REASON_CODE,
            "description": P02_DESCRIPTION,
            "change_summary": BYPASS_NOTE,
        },
    )

    # Activating a rule is a material action (FR-1104); this one had no
    # approver, and the audit trail says so rather than staying silent.
    bind.execute(
        sa.text(
            "INSERT INTO audit_log (correlation_id, actor_user_id, actor_role, object_type, object_id, "
            "from_state, to_state, reason, action, details) "
            "VALUES (:correlation_id, NULL, NULL, 'RULE_VERSION', :object_id, NULL, 'ACTIVE', :reason, "
            "'RULE_SEEDED_ACTIVE', CAST(:details AS jsonb))"
        ),
        {
            "correlation_id": P02_CORRELATION_ID,
            "object_id": P02_V1_ID,
            "reason": BYPASS_NOTE,
            "details": json.dumps(
                {
                    "rule_ref": "RUL-0001",
                    "version": 1,
                    "maker_checker": "BYPASSED",
                    "awaiting": ["FR-304", "ROLE_MLRO"],
                    "parameters": P02_PARAMETERS,
                }
            ),
        },
    )

    # FR-302 flow 3 / BR-302.2: an alert references the exact version that
    # produced it. Every existing alert is P02 and was raised with these
    # parameters, so it belongs to v1.
    op.add_column("alert", sa.Column("rule_version_id", postgresql.UUID(as_uuid=True), nullable=True))
    bind.execute(
        sa.text("UPDATE alert SET rule_version_id = :version_id WHERE pattern_code = 'P02_STRUCTURING'"),
        {"version_id": P02_V1_ID},
    )
    op.alter_column("alert", "rule_version_id", nullable=False)
    op.create_foreign_key("fk_alert_rule_version_id", "alert", "rule_version", ["rule_version_id"], ["id"])
    op.create_index(op.f("ix_alert_rule_version_id"), "alert", ["rule_version_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_alert_rule_version_id"), table_name="alert")
    op.drop_constraint("fk_alert_rule_version_id", "alert", type_="foreignkey")
    op.drop_column("alert", "rule_version_id")

    # Undoing the seed means removing rows the guards exist to protect, so they
    # are lifted for exactly this statement block. Anything built on RUL-0001
    # after the seed (later versions, their simulations) goes with it.
    bind = op.get_bind()
    for table, trigger in (
        ("simulation_result", "simulation_result_append_only"),
        ("rule_version", "rule_version_guard"),
        ("rule", "rule_append_only"),
    ):
        bind.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
    params = {"rule_id": P02_RULE_ID}
    bind.execute(
        sa.text(
            "DELETE FROM simulation_result WHERE rule_version_id IN "
            "(SELECT id FROM rule_version WHERE rule_id = :rule_id)"
        ),
        params,
    )
    # One statement: the lineage foreign key is checked at its end, so the
    # whole chain of versions goes together.
    bind.execute(sa.text("DELETE FROM rule_version WHERE rule_id = :rule_id"), params)
    bind.execute(sa.text("DELETE FROM rule WHERE id = :rule_id"), params)
    for table, trigger in (
        ("simulation_result", "simulation_result_append_only"),
        ("rule_version", "rule_version_guard"),
        ("rule", "rule_append_only"),
    ):
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))
    bind.execute(
        sa.text("DELETE FROM audit_log WHERE action = 'RULE_SEEDED_ACTIVE' AND object_id = :version_id"),
        {"version_id": P02_V1_ID},
    )
    # rule_ref_seq is deliberately not rewound: rules created after the seed
    # keep their numbers, and RUL-0001 is simply never reissued.
