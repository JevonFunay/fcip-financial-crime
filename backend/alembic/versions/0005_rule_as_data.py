"""rules as data: rule, rule_version, simulation_result (FR-301..FR-303)

Revision ID: 0005_rule_as_data
Revises: 0004_ingestion_batch
Create Date: 2026-09-29

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0005_rule_as_data"
down_revision: Union[str, None] = "0004_ingestion_batch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RULE_STATES = ("DRAFT", "IN_SIMULATION", "PENDING_APPROVAL", "ACTIVE", "SUSPENDED", "RETIRED", "REJECTED")


def upgrade() -> None:
    # RUL-0001, ... gap-tolerant, same approach as case_number_seq.
    op.execute("CREATE SEQUENCE IF NOT EXISTS rule_ref_seq START 1")

    op.create_table(
        "rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("rule_ref", sa.String(), nullable=False),
        sa.Column("pattern_code", sa.String(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("btrim(pattern_code) <> ''", name="ck_rule_pattern_code_present"),
    )
    op.create_index(op.f("ix_rule_rule_ref"), "rule", ["rule_ref"], unique=True)
    op.create_index(op.f("ix_rule_pattern_code"), "rule", ["pattern_code"])
    op.create_index(op.f("ix_rule_correlation_id"), "rule", ["correlation_id"])

    op.create_table(
        "rule_version",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("rule_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version", sa.Integer(), nullable=True),
        sa.Column("template_version", sa.String(), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("window_type", sa.Enum("ROLLING", "CALENDAR", name="rule_window_type"), nullable=False),
        sa.Column("window_length", sa.Interval(), nullable=False),
        sa.Column(
            "entity_scope",
            sa.Enum("CUSTOMER", "MERCHANT", "ACCOUNT", "DEVICE", name="rule_entity_scope"),
            nullable=False,
        ),
        # FRD §8.0 severity scale, declared low-to-high so it orders by severity.
        sa.Column("severity", sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="rule_severity"), nullable=False),
        sa.Column("reason_code", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("state", sa.Enum(*RULE_STATES, name="rule_state"), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("changed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["rule_id"], ["rule.id"]),
        sa.ForeignKeyConstraint(["changed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "version", name="uq_rule_version_rule_id_version"),
        # A parent is always an earlier version of the same rule; only v1 has none.
        sa.ForeignKeyConstraint(
            ["rule_id", "parent_version"],
            ["rule_version.rule_id", "rule_version.version"],
            name="fk_rule_version_parent",
        ),
        # IS NOT NULL is explicit because a CHECK that evaluates to NULL passes.
        sa.CheckConstraint(
            "(version = 1 AND parent_version IS NULL) "
            "OR (version > 1 AND parent_version IS NOT NULL AND parent_version < version)",
            name="ck_rule_version_lineage",
        ),
        sa.CheckConstraint("window_length > interval '0'", name="ck_rule_version_window_length_positive"),
        sa.CheckConstraint("jsonb_typeof(parameters) = 'object'", name="ck_rule_version_parameters_object"),
        # FR-301 AC1: never saved without a reason code, severity (NOT NULL
        # above) and description.
        sa.CheckConstraint("btrim(reason_code) <> ''", name="ck_rule_version_reason_code_present"),
        sa.CheckConstraint("btrim(description) <> ''", name="ck_rule_version_description_present"),
        sa.CheckConstraint("btrim(change_summary) <> ''", name="ck_rule_version_change_summary_present"),
    )
    op.create_index(op.f("ix_rule_version_rule_id"), "rule_version", ["rule_id"])
    # FR-301 AC2: an ACTIVE reason code cannot be duplicated (case-insensitive,
    # so RC-STRUCT-01 and rc-struct-01 cannot both be live).
    op.execute(
        "CREATE UNIQUE INDEX uq_rule_version_active_reason_code "
        "ON rule_version (upper(reason_code)) WHERE state = 'ACTIVE'"
    )
    # At most one ACTIVE version per rule, or a detection run would double-alert.
    op.execute(
        "CREATE UNIQUE INDEX uq_rule_version_one_active_per_rule ON rule_version (rule_id) WHERE state = 'ACTIVE'"
    )

    # FR-302 / BR-302.1 / TRD §7.3, enforced in the database so no code path can
    # skip it. Only `state` may ever change: a change to what a rule does is a
    # new version. An ACTIVE version may only be suspended or retired. No
    # version is ever deleted. (TRUNCATE does not fire row triggers; only the
    # test suite's cleanup uses it.)
    op.execute(
        """
        CREATE FUNCTION rule_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'RULE_VERSION_IMMUTABLE: rule versions are never deleted (BR-302.1)';
            END IF;
            IF (to_jsonb(NEW) - 'state') IS DISTINCT FROM (to_jsonb(OLD) - 'state') THEN
                RAISE EXCEPTION
                    'RULE_VERSION_IMMUTABLE: version % of rule % cannot be edited; create a new version (FR-302)',
                    OLD.version, OLD.rule_id;
            END IF;
            IF OLD.state = 'ACTIVE' AND NEW.state <> 'ACTIVE' AND NEW.state NOT IN ('SUSPENDED', 'RETIRED') THEN
                RAISE EXCEPTION
                    'RULE_VERSION_IMMUTABLE: an ACTIVE version can only move to SUSPENDED or RETIRED, not %',
                    NEW.state;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER rule_version_guard BEFORE UPDATE OR DELETE ON rule_version "
        "FOR EACH ROW EXECUTE FUNCTION rule_version_guard()"
    )

    op.create_table(
        "simulation_result",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("rule_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transactions_scanned", sa.Integer(), nullable=False),
        sa.Column("would_be_alert_count", sa.Integer(), nullable=False),
        sa.Column("unique_entity_count", sa.Integer(), nullable=False),
        sa.Column("daily_distribution", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("overlap_by_rule", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sample_alerts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("run_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["rule_version_id"], ["rule_version.id"]),
        sa.ForeignKeyConstraint(["run_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("period_end > period_start", name="ck_simulation_result_period"),
        sa.CheckConstraint(
            "transactions_scanned >= 0 AND unique_entity_count >= 0 AND would_be_alert_count >= unique_entity_count",
            name="ck_simulation_result_counts",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(daily_distribution) = 'object' AND jsonb_typeof(overlap_by_rule) = 'array' "
            "AND jsonb_typeof(sample_alerts) = 'array' AND jsonb_array_length(sample_alerts) <= 20",
            name="ck_simulation_result_shapes",
        ),
    )
    op.create_index(op.f("ix_simulation_result_rule_version_id"), "simulation_result", ["rule_version_id"])

    # A rule's identity and a simulation's result are written once and never
    # changed: the result is the evidence an activation request relies on.
    op.execute(
        """
        CREATE FUNCTION append_only_guard() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'APPEND_ONLY: rows of % are never updated or deleted', TG_TABLE_NAME;
        END
        $$
        """
    )
    for table in ("rule", "simulation_result"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION append_only_guard()"
        )

    # TRD §8.8 `action` plus structured detail (e.g. a parameter-level diff).
    # Nullable: existing events carry their meaning in the state transition.
    op.add_column("audit_log", sa.Column("action", sa.String(), nullable=True))
    op.add_column("audit_log", sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index(op.f("ix_audit_log_action"), "audit_log", ["action"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_log_action"), table_name="audit_log")
    op.drop_column("audit_log", "details")
    op.drop_column("audit_log", "action")

    for table in ("simulation_result", "rule"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS append_only_guard()")
    op.drop_index(op.f("ix_simulation_result_rule_version_id"), table_name="simulation_result")
    op.drop_table("simulation_result")

    op.execute("DROP TRIGGER IF EXISTS rule_version_guard ON rule_version")
    op.execute("DROP FUNCTION IF EXISTS rule_version_guard()")
    op.execute("DROP INDEX IF EXISTS uq_rule_version_one_active_per_rule")
    op.execute("DROP INDEX IF EXISTS uq_rule_version_active_reason_code")
    op.drop_index(op.f("ix_rule_version_rule_id"), table_name="rule_version")
    op.drop_table("rule_version")

    op.drop_index(op.f("ix_rule_correlation_id"), table_name="rule")
    op.drop_index(op.f("ix_rule_pattern_code"), table_name="rule")
    op.drop_index(op.f("ix_rule_rule_ref"), table_name="rule")
    op.drop_table("rule")

    for enum_name in ("rule_state", "rule_severity", "rule_entity_scope", "rule_window_type"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
    op.execute("DROP SEQUENCE IF EXISTS rule_ref_seq")
