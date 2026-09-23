"""ingestion batch (FR-101) and processing log (FR-105)

Revision ID: 0004_ingestion_batch
Revises: 0003_case_number_sequence
Create Date: 2026-09-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0004_ingestion_batch"
down_revision: Union[str, None] = "0003_case_number_sequence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Human-readable, gap-tolerant batch references (BAT-000001, ...), same
    # approach as case_number_seq.
    op.execute("CREATE SEQUENCE IF NOT EXISTS batch_ref_seq START 1")

    op.create_table(
        "ingestion_batch",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("batch_ref", sa.String(), nullable=False),
        sa.Column("source_system", sa.String(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("file_name", sa.String(), nullable=False),
        sa.Column("file_checksum", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_records", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("REGISTERED", "COMPLETED", "NEEDS_REVIEW", "FAILED", name="ingestion_batch_status"),
            nullable=False,
        ),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accepted_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quarantined_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("registered_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["registered_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # FR-101 / TRD C-01: the same file cannot be registered twice for the
        # same source system and business date.
        sa.UniqueConstraint(
            "source_system", "business_date", "file_checksum", name="uq_batch_source_business_date_checksum"
        ),
    )
    op.create_index(op.f("ix_ingestion_batch_batch_ref"), "ingestion_batch", ["batch_ref"], unique=True)
    op.create_index(op.f("ix_ingestion_batch_business_date"), "ingestion_batch", ["business_date"])
    op.create_index(op.f("ix_ingestion_batch_file_checksum"), "ingestion_batch", ["file_checksum"])
    op.create_index(op.f("ix_ingestion_batch_correlation_id"), "ingestion_batch", ["correlation_id"])

    op.create_table(
        "processing_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        # Insertion order: `now()` is transaction start time in Postgres, so
        # entries written in one transaction cannot be ordered by created_at.
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_code", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["ingestion_batch_id"], ["ingestion_batch.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
    )
    op.create_index(op.f("ix_processing_log_ingestion_batch_id"), "processing_log", ["ingestion_batch_id"])

    for table in ("transaction", "quarantine_item"):
        op.add_column(table, sa.Column("ingestion_batch_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_ingestion_batch_id", table, "ingestion_batch", ["ingestion_batch_id"], ["id"]
        )
        op.create_index(f"ix_{table}_ingestion_batch_id", table, ["ingestion_batch_id"])


def downgrade() -> None:
    for table in ("quarantine_item", "transaction"):
        op.drop_index(f"ix_{table}_ingestion_batch_id", table_name=table)
        op.drop_constraint(f"fk_{table}_ingestion_batch_id", table, type_="foreignkey")
        op.drop_column(table, "ingestion_batch_id")

    op.drop_index(op.f("ix_processing_log_ingestion_batch_id"), table_name="processing_log")
    op.drop_table("processing_log")

    op.drop_index(op.f("ix_ingestion_batch_correlation_id"), table_name="ingestion_batch")
    op.drop_index(op.f("ix_ingestion_batch_file_checksum"), table_name="ingestion_batch")
    op.drop_index(op.f("ix_ingestion_batch_business_date"), table_name="ingestion_batch")
    op.drop_index(op.f("ix_ingestion_batch_batch_ref"), table_name="ingestion_batch")
    op.drop_table("ingestion_batch")

    sa.Enum(name="ingestion_batch_status").drop(op.get_bind(), checkfirst=True)
    op.execute("DROP SEQUENCE IF EXISTS batch_ref_seq")
