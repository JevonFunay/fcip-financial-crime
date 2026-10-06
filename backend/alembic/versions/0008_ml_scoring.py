"""model versions, their scores, and the ML_SCORE rule seeded DRAFT

Revision ID: 0008_ml_scoring
Revises: 0007_transaction_context
Create Date: 2026-10-06

FR-401/FR-405, TRD §10.2 and §10.5. A model version is registered once and its
content never changes; a score is written once and never rewritten (BR-405.1,
FR-405 AC3). Both are enforced here, like the rule versions in 0005.

CF-05. A model score shows on alerts as a supporting factor (option a). A
score raising its own alert (option b, the mentor's direction, awaiting written
confirmation) goes through a rule, as FRD BR-401.2 requires: "kecuali sebuah
anomaly rule dikonfigurasi dan disetujui secara eksplisit melalui §5.3". That
rule is seeded here as RUL-0002, pattern ML_SCORE, in state DRAFT: it raises
nothing until it is activated, which is a state change, not a code change.
Its reason code RC-ML-01 and its severity are ours and await ratification.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_ml_scoring"
down_revision: Union[str, None] = "0007_transaction_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ML_RULE_ID = "6f0d2a51-8c3e-4b7a-9e15-2d4c7b8a90f3"
ML_V1_ID = "b81c4e2f-5a07-4d93-8f6e-0c3a9d5e1b72"
ML_CORRELATION_ID = "d2a7f4c9-1e86-4b30-a5d2-7f9c3e0b4a18"

ML_PARAMETERS = {
    # Which registered model versions the rule reads, per unit; each version's
    # own threshold (chosen on validation, in its model card) applies.
    "models": {"AML": "aml_gbm_v1", "FRAUD": "fraud_gbm_v1"},
    "threshold": "model_version.threshold",
    # A Fraud score is per transaction; flagged transactions of one entity on
    # one local day become one alert, so a burst is one finding, not fifty.
    "fraud_grouping": "entity, local day (Asia/Jakarta)",
}
ML_REASON_CODE = "RC-ML-01"
ML_SEVERITY = "MEDIUM"
ML_DESCRIPTION = (
    "Observes that a registered detection model scored the entity's activity at or above that model "
    "version's threshold; the alert names the features that contributed most to the score."
)
SEED_NOTE = (
    "Seeded DRAFT by migration 0008 for CF-05 option (b), which awaits the mentor's written confirmation. "
    "It raises nothing until activated (a state change through FR-304 once built). Reason code RC-ML-01 "
    "and severity MEDIUM are the team's proposal, not FRD values, and await ratification."
)

_GUARD = """
CREATE FUNCTION model_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'MODEL_VERSION_IMMUTABLE: model versions are never deleted (FR-405)';
    END IF;
    -- Only the lifecycle may move: state, approval provenance, effective_from,
    -- superseded_by. The model, its threshold and its card are the version.
    IF (to_jsonb(NEW) - 'state' - 'submitted_by' - 'approved_by' - 'approved_at' - 'simulation_reference'
            - 'effective_from' - 'superseded_by')
       IS DISTINCT FROM
       (to_jsonb(OLD) - 'state' - 'submitted_by' - 'approved_by' - 'approved_at' - 'simulation_reference'
            - 'effective_from' - 'superseded_by') THEN
        RAISE EXCEPTION 'MODEL_VERSION_IMMUTABLE: % cannot be edited; register a new version (FR-405)',
            OLD.version;
    END IF;
    IF OLD.state = 'ACTIVE' AND NEW.state <> 'ACTIVE' AND NEW.state NOT IN ('SUSPENDED', 'RETIRED') THEN
        RAISE EXCEPTION 'MODEL_VERSION_IMMUTABLE: an ACTIVE version can only move to SUSPENDED or RETIRED, not %',
            NEW.state;
    END IF;
    RETURN NEW;
END
$$
"""


def upgrade() -> None:
    rule_state = postgresql.ENUM(name="rule_state", create_type=False)
    op.create_table(
        "model_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("artefact_type", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("state", rule_state, nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("submitted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("simulation_reference", sa.String(), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("feature_set_version", sa.String(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("model_text", sa.Text(), nullable=False),
        sa.Column("model_card", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("unit IN ('AML', 'FRAUD')", name="ck_model_version_unit"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["submitted_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["superseded_by"], ["model_version.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version"),
    )
    # One live model per unit, or a run would score twice.
    op.execute("CREATE UNIQUE INDEX uq_model_version_one_active_per_unit ON model_version (unit) WHERE state = 'ACTIVE'")
    op.execute(_GUARD)
    op.execute(
        "CREATE TRIGGER model_version_guard BEFORE UPDATE OR DELETE ON model_version "
        "FOR EACH ROW EXECUTE FUNCTION model_version_guard()"
    )

    op.create_table(
        "ml_score",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("unit", sa.String(), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False),
        sa.Column("contributions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("detection_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("unit IN ('AML', 'FRAUD')", name="ck_ml_score_unit"),
        sa.CheckConstraint("(unit = 'FRAUD') = (transaction_id IS NOT NULL)", name="ck_ml_score_unit_shape"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_version.id"]),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.ForeignKeyConstraint(["transaction_id"], ["transaction.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("model_version_id", "customer_id", "transaction_id", "as_of", "flagged", "detection_run_id"):
        op.create_index(op.f(f"ix_ml_score_{column}"), "ml_score", [column])
    op.execute(
        "CREATE UNIQUE INDEX uq_ml_score_transaction ON ml_score (model_version_id, transaction_id) "
        "WHERE transaction_id IS NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_ml_score_entity_week ON ml_score (model_version_id, customer_id, as_of) "
        "WHERE transaction_id IS NULL"
    )
    # BR-405.1 / FR-405 AC3: a stored score is never rewritten. The function
    # comes from migration 0005.
    op.execute(
        "CREATE TRIGGER ml_score_append_only BEFORE UPDATE OR DELETE ON ml_score "
        "FOR EACH ROW EXECUTE FUNCTION append_only_guard()"
    )

    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO rule (id, rule_ref, pattern_code, correlation_id, created_by) "
            "VALUES (:id, 'RUL-0002', 'ML_SCORE', :correlation_id, NULL)"
        ),
        {"id": ML_RULE_ID, "correlation_id": ML_CORRELATION_ID},
    )
    # Fixed like RUL-0001, so the seed is identical everywhere; the sequence
    # only moves forward, past it.
    bind.execute(sa.text("SELECT setval('rule_ref_seq', GREATEST(2, (SELECT last_value FROM rule_ref_seq)))"))
    bind.execute(
        sa.text(
            "INSERT INTO rule_version (id, rule_id, version, parent_version, template_version, parameters, "
            "window_type, window_length, entity_scope, severity, reason_code, description, state, "
            "change_summary, changed_by) "
            "VALUES (:id, :rule_id, 1, NULL, 'ml_score_v1', CAST(:parameters AS jsonb), "
            "'CALENDAR', interval '7 days', 'CUSTOMER', :severity, :reason_code, :description, 'DRAFT', "
            ":change_summary, NULL)"
        ),
        {
            "id": ML_V1_ID,
            "rule_id": ML_RULE_ID,
            "parameters": json.dumps(ML_PARAMETERS),
            "severity": ML_SEVERITY,
            "reason_code": ML_REASON_CODE,
            "description": ML_DESCRIPTION,
            "change_summary": SEED_NOTE,
        },
    )
    bind.execute(
        sa.text(
            "INSERT INTO audit_log (correlation_id, actor_user_id, actor_role, object_type, object_id, "
            "from_state, to_state, reason, action, details) "
            "VALUES (:correlation_id, NULL, NULL, 'RULE_VERSION', :object_id, NULL, 'DRAFT', :reason, "
            "'RULE_SEEDED_DRAFT', CAST(:details AS jsonb))"
        ),
        {
            "correlation_id": ML_CORRELATION_ID,
            "object_id": ML_V1_ID,
            "reason": SEED_NOTE,
            "details": json.dumps({"pattern_code": "ML_SCORE", "version": 1, "awaiting": ["CF-05", "RC-ML-01"],
                                   "parameters": ML_PARAMETERS}),
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM audit_log WHERE action = 'RULE_SEEDED_DRAFT' AND object_id = :id"),
                 {"id": ML_V1_ID})
    for table, trigger in (("rule_version", "rule_version_guard"), ("rule", "rule_append_only")):
        bind.execute(sa.text(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}"))
    bind.execute(sa.text("DELETE FROM alert_transaction WHERE alert_id IN (SELECT id FROM alert WHERE rule_version_id = :v)"),
                 {"v": ML_V1_ID})
    bind.execute(sa.text("DELETE FROM alert WHERE rule_version_id = :v"), {"v": ML_V1_ID})
    bind.execute(sa.text("DELETE FROM rule_version WHERE rule_id = :r"), {"r": ML_RULE_ID})
    bind.execute(sa.text("DELETE FROM rule WHERE id = :r"), {"r": ML_RULE_ID})
    for table, trigger in (("rule_version", "rule_version_guard"), ("rule", "rule_append_only")):
        bind.execute(sa.text(f"ALTER TABLE {table} ENABLE TRIGGER {trigger}"))

    op.execute("DROP TRIGGER IF EXISTS ml_score_append_only ON ml_score")
    op.execute("DROP INDEX IF EXISTS uq_ml_score_entity_week")
    op.execute("DROP INDEX IF EXISTS uq_ml_score_transaction")
    for column in ("model_version_id", "customer_id", "transaction_id", "as_of", "flagged", "detection_run_id"):
        op.drop_index(op.f(f"ix_ml_score_{column}"), table_name="ml_score")
    op.drop_table("ml_score")
    op.execute("DROP TRIGGER IF EXISTS model_version_guard ON model_version")
    op.execute("DROP FUNCTION IF EXISTS model_version_guard()")
    op.execute("DROP INDEX IF EXISTS uq_model_version_one_active_per_unit")
    op.drop_table("model_version")
