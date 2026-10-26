"""Model lab: labelled training datasets, training runs, candidate models.

Revision ID: 0010_model_lab
Revises: 0009_behavioral_context
Create Date: 2026-10-26

Prototype tables for the framework direction. They are separate from the live
`transaction` table on purpose: a training dataset carries labels and is never
monitored. Saved candidates go to the existing `model_version` registry as
DRAFT, so nothing here can activate scoring.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010_model_lab"
down_revision = "0009_behavioral_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lab_dataset",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("raw_csv", sa.LargeBinary(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("columns", postgresql.JSONB(), nullable=False),
        sa.Column("preview", postgresql.JSONB(), nullable=False),
        sa.Column("suggestion", postgresql.JSONB(), nullable=False),
        sa.Column("mapping", postgresql.JSONB(), nullable=True),
        sa.Column("validation", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('UPLOADED', 'MAPPED')", name="ck_lab_dataset_status"),
    )
    op.create_index("ix_lab_dataset_file_sha256", "lab_dataset", ["file_sha256"])

    op.create_table(
        "lab_run",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("lab_dataset.id"), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("results", postgresql.JSONB(), nullable=False),
        sa.Column("seconds", sa.Float(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_lab_run_dataset_id", "lab_run", ["dataset_id"])

    op.create_table(
        "lab_model",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("lab_run.id"), nullable=False),
        sa.Column("model_key", sa.String(), nullable=False),
        sa.Column("artefact_type", sa.String(), nullable=False),
        sa.Column("model_text", sa.Text(), nullable=False),
        sa.Column("features", postgresql.JSONB(), nullable=False),
        sa.Column("vocabulary", postgresql.JSONB(), nullable=False),
        sa.Column("thresholds", postgresql.JSONB(), nullable=False),
        sa.Column("test_curve", postgresql.JSONB(), nullable=False),
        sa.Column("default_index", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("run_id", "model_key", name="uq_lab_model_run_key"),
    )
    op.create_index("ix_lab_model_run_id", "lab_model", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_lab_model_run_id", table_name="lab_model")
    op.drop_table("lab_model")
    op.drop_index("ix_lab_run_dataset_id", table_name="lab_run")
    op.drop_table("lab_run")
    op.drop_index("ix_lab_dataset_file_sha256", table_name="lab_dataset")
    op.drop_table("lab_dataset")
