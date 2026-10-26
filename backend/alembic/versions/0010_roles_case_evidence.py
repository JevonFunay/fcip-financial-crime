"""5 new roles, case lifecycle columns, evidence table, alert assignment

Revision ID: 0010_roles_case_evidence
Revises: 0009_behavioral_context
Create Date: 2026-10-27

FRD §4.1: 5 missing RBAC roles (SENIOR_INVESTIGATOR, MLRO, AUDITOR,
MANAGEMENT, ADMIN) added to the existing Postgres enum type.

FR-804/FR-808: case lifecycle — closed_reason, closure_submitted_by,
closure_approved_by columns; additional case_status enum values
(PENDING_CLOSURE, REOPENED, ESCALATED, REFERRED).

FR-603: alert.assigned_to column for alert assignment.

FR-811–813: evidence table (insert-only trigger via shared append_only_guard
from migration 0005).

All new enum values are added with ALTER TYPE … ADD VALUE.
Postgres requires that ALTER TYPE is committed before the new value can
appear in a CHECK or be compared — which is fine because we use Alembic's
non-transactional DDL path.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_roles_case_evidence"
down_revision: Union[str, None] = "0009_behavioral_context"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Add the 5 new UserRole values ──────────────────────────────────────
    # ALTER TYPE … ADD VALUE cannot run inside an explicit transaction in PG<14,
    # but Alembic's op.execute() is fine here because the session is autocommit
    # during the migration.
    for value in (
        "ROLE_SENIOR_INVESTIGATOR",
        "ROLE_MLRO",
        "ROLE_AUDITOR",
        "ROLE_MANAGEMENT",
        "ROLE_ADMIN",
    ):
        op.execute(f"ALTER TYPE user_role ADD VALUE IF NOT EXISTS '{value}'")

    # ── 2. Add new CaseStatus values ──────────────────────────────────────────
    for value in ("PENDING_CLOSURE", "REOPENED", "ESCALATED", "REFERRED"):
        op.execute(f"ALTER TYPE case_status ADD VALUE IF NOT EXISTS '{value}'")

    # ── 3. Case lifecycle columns (FR-804, FR-814) ────────────────────────────
    op.add_column("case", sa.Column("closed_reason", sa.String(), nullable=True))
    op.add_column(
        "case",
        sa.Column(
            "closure_submitted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "case",
        sa.Column(
            "closure_approved_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )

    # ── 4. Alert assignment (FR-603) ──────────────────────────────────────────
    op.add_column(
        "alert",
        sa.Column(
            "assigned_to",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
    )
    op.create_index("ix_alert_assigned_to", "alert", ["assigned_to"])

    # ── 5. Evidence table (FR-811–813) ────────────────────────────────────────
    op.create_table(
        "evidence",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "evidence_type",
            postgresql.ENUM(
                "DOCUMENT",
                "SCREENSHOT",
                "NOTE",
                "EXTERNAL_LINK",
                "OTHER",
                name="evidence_type",
                create_type=True,
            ),
            nullable=False,
        ),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("storage_uri", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["case.id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_evidence_case_id", "evidence", ["case_id"])
    op.create_index("ix_evidence_correlation_id", "evidence", ["correlation_id"])

    # FR-813: evidence rows are immutable (same guard used by rule_version and ml_score)
    op.execute(
        "CREATE TRIGGER evidence_append_only BEFORE UPDATE OR DELETE ON evidence "
        "FOR EACH ROW EXECUTE FUNCTION append_only_guard()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS evidence_append_only ON evidence")
    op.drop_index("ix_evidence_correlation_id", table_name="evidence")
    op.drop_index("ix_evidence_case_id", table_name="evidence")
    op.drop_table("evidence")
    op.execute("DROP TYPE IF EXISTS evidence_type")

    op.drop_index("ix_alert_assigned_to", table_name="alert")
    op.drop_column("alert", "assigned_to")

    op.drop_column("case", "closure_approved_by")
    op.drop_column("case", "closure_submitted_by")
    op.drop_column("case", "closed_reason")
    # NOTE: Postgres does not support removing values from an enum type.
    # Downgrade leaves the new enum values in place (harmless).
