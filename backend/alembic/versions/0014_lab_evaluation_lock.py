"""Seal one final choice per exact source file and preserve run provenance.

Revision ID: 0014_lab_evaluation_lock
Revises: 0013_seed_pattern_rules
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_lab_evaluation_lock"
down_revision = "0013_seed_pattern_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lab_evaluation_protocol",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_file_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("protocol_version", sa.String(), nullable=False),
        sa.Column("mapping_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("mapping_sha256", sa.String(64), nullable=True),
        sa.Column("train_share", sa.Float(), nullable=True),
        sa.Column("legacy_exposed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("final_run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("lab_run.id"), nullable=True),
        sa.Column("final_model_key", sa.String(), nullable=True),
        sa.Column("final_threshold_index", sa.Integer(), nullable=True),
        sa.Column("final_model_version_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("model_version.id"), nullable=True),
        sa.Column("final_locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("legacy_exposed OR (mapping_snapshot IS NOT NULL AND mapping_sha256 IS NOT NULL "
                           "AND train_share IS NOT NULL AND train_share >= 0.5 AND train_share <= 0.8)", name="ck_lab_protocol_contract"),
        sa.CheckConstraint("(final_run_id IS NULL AND final_model_key IS NULL AND final_threshold_index IS NULL "
                           "AND final_locked_at IS NULL AND final_model_version_id IS NULL AND final_evaluated_at IS NULL) "
                           "OR (final_run_id IS NOT NULL AND final_model_key IS NOT NULL AND final_threshold_index IS NOT NULL AND final_threshold_index >= 0 "
                           "AND final_locked_at IS NOT NULL)", name="ck_lab_final_choice"),
        sa.CheckConstraint("(final_model_version_id IS NULL AND final_evaluated_at IS NULL) OR "
                           "(final_model_version_id IS NOT NULL AND final_evaluated_at IS NOT NULL)",
                           name="ck_lab_final_reveal_pair"),
        sa.CheckConstraint("NOT legacy_exposed OR final_run_id IS NULL", name="ck_lab_legacy_not_fresh"),
    )
    op.add_column("lab_run", sa.Column("protocol_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_lab_run_protocol", "lab_run", "lab_evaluation_protocol", ["protocol_id"], ["id"])
    # Old comparisons already exposed test outcomes. A duplicate upload of the
    # same bytes must not launder that exposure into a new 'unseen' final.
    op.execute("""
        INSERT INTO lab_evaluation_protocol (source_file_sha256, protocol_version, legacy_exposed)
        SELECT DISTINCT d.file_sha256, 'legacy_exposed_v1', TRUE
        FROM lab_dataset d JOIN lab_run r ON r.dataset_id = d.id
    """)
    op.execute("""
        CREATE FUNCTION guard_lab_protocol() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'LAB_PROTOCOL_IMMUTABLE'; END IF;
          IF ROW(NEW.id, NEW.source_file_sha256, NEW.protocol_version, NEW.mapping_snapshot,
                 NEW.mapping_sha256, NEW.train_share, NEW.legacy_exposed, NEW.created_at)
             IS DISTINCT FROM
             ROW(OLD.id, OLD.source_file_sha256, OLD.protocol_version, OLD.mapping_snapshot,
                 OLD.mapping_sha256, OLD.train_share, OLD.legacy_exposed, OLD.created_at)
          THEN RAISE EXCEPTION 'LAB_PROTOCOL_IMMUTABLE'; END IF;
          IF OLD.final_run_id IS NOT NULL AND
             ROW(NEW.final_run_id, NEW.final_model_key, NEW.final_threshold_index, NEW.final_locked_at)
             IS DISTINCT FROM
             ROW(OLD.final_run_id, OLD.final_model_key, OLD.final_threshold_index, OLD.final_locked_at)
          THEN RAISE EXCEPTION 'LAB_FINAL_CHOICE_IMMUTABLE'; END IF;
          IF OLD.final_model_version_id IS NOT NULL AND NEW IS DISTINCT FROM OLD
          THEN RAISE EXCEPTION 'LAB_FINAL_ALREADY_REVEALED'; END IF;
          IF NEW.final_run_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM lab_run r WHERE r.id = NEW.final_run_id AND r.protocol_id = NEW.id)
          THEN RAISE EXCEPTION 'LAB_FINAL_RUN_PROTOCOL_MISMATCH'; END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER lab_protocol_guard BEFORE UPDATE OR DELETE ON lab_evaluation_protocol
        FOR EACH ROW EXECUTE FUNCTION guard_lab_protocol();
    """)
    for table, error in (("lab_run", "LAB_RUN_IMMUTABLE"), ("lab_model", "LAB_CANDIDATE_IMMUTABLE")):
        op.execute(f"""
            CREATE FUNCTION guard_{table}() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION '{error}'; END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER {table}_guard BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION guard_{table}();
        """)
    op.execute("""
        CREATE FUNCTION guard_lab_dataset_contract() RETURNS trigger AS $$
        BEGIN
          IF ROW(NEW.id, NEW.file_sha256, NEW.raw_file, NEW.file_format, NEW.row_count,
                 NEW.file_size_bytes, NEW.columns, NEW.preview, NEW.suggestion)
             IS DISTINCT FROM
             ROW(OLD.id, OLD.file_sha256, OLD.raw_file, OLD.file_format, OLD.row_count,
                 OLD.file_size_bytes, OLD.columns, OLD.preview, OLD.suggestion)
          THEN RAISE EXCEPTION 'LAB_SOURCE_IMMUTABLE'; END IF;
          IF EXISTS (SELECT 1 FROM lab_evaluation_protocol p WHERE p.source_file_sha256 = OLD.file_sha256
             AND ((p.legacy_exposed AND ROW(NEW.mapping, NEW.validation) IS DISTINCT FROM ROW(OLD.mapping, OLD.validation))
               OR (NOT p.legacy_exposed AND (NEW.mapping IS DISTINCT FROM p.mapping_snapshot
                   OR (OLD.mapping IS NOT NULL AND ROW(NEW.mapping, NEW.validation) IS DISTINCT FROM ROW(OLD.mapping, OLD.validation))))))
          THEN RAISE EXCEPTION 'LAB_MAPPING_LOCKED'; END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER lab_dataset_contract_guard BEFORE UPDATE ON lab_dataset
        FOR EACH ROW EXECUTE FUNCTION guard_lab_dataset_contract();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER lab_dataset_contract_guard ON lab_dataset; DROP FUNCTION guard_lab_dataset_contract()")
    for table in ("lab_model", "lab_run"):
        op.execute(f"DROP TRIGGER {table}_guard ON {table}; DROP FUNCTION guard_{table}()")
    op.execute("DROP TRIGGER lab_protocol_guard ON lab_evaluation_protocol; DROP FUNCTION guard_lab_protocol()")
    op.drop_constraint("fk_lab_run_protocol", "lab_run", type_="foreignkey")
    op.drop_column("lab_run", "protocol_id")
    op.drop_table("lab_evaluation_protocol")
