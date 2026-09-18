"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.Enum("ROLE_DATA_OPS", "ROLE_ANALYST", "ROLE_TRIAGE", "ROLE_INVESTIGATOR", name="user_role"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("refresh_token_hash"),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

    op.create_table(
        "customer",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("customer_ref", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        sa.Column("entity_type", sa.Enum("INDIVIDUAL", "BUSINESS", name="entity_type"), nullable=False),
        sa.Column("id_number", sa.String(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("customer_ref"),
    )
    op.create_index("ix_customer_customer_ref", "customer", ["customer_ref"])

    op.create_table(
        "account",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("account_number", sa.String(), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("account_type", sa.String(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "CLOSED", name="account_status"),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("opened_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_number"),
    )
    op.create_index("ix_account_account_number", "account", ["account_number"])
    op.create_index("ix_account_customer_id", "account", ["customer_id"])

    op.create_table(
        "transaction",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("transaction_ref", sa.String(), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("account.id"), nullable=False),
        sa.Column("transaction_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("direction", sa.Enum("CREDIT", "DEBIT", name="transaction_direction"), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("counterparty_ref", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("transaction_ref"),
    )
    op.create_index("ix_transaction_transaction_ref", "transaction", ["transaction_ref"])
    op.create_index("ix_transaction_account_id", "transaction", ["account_id"])
    op.create_index("ix_transaction_transaction_date", "transaction", ["transaction_date"])

    op.create_table(
        "case",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("case_number", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "IN_PROGRESS", "CLOSED", name="case_status"),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("opened_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("assigned_to", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("case_number"),
    )
    op.create_index("ix_case_case_number", "case", ["case_number"])

    op.create_table(
        "alert",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("pattern_code", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("OPEN", "DISPOSED", "ESCALATED", name="alert_status"),
            nullable=False,
            server_default="OPEN",
        ),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("detection_details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("case.id"), nullable=True),
        sa.Column("disposed_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition_reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_alert_pattern_code", "alert", ["pattern_code"])
    op.create_index("ix_alert_customer_id", "alert", ["customer_id"])

    op.create_table(
        "alert_transaction",
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("alert.id"), primary_key=True),
        sa.Column(
            "transaction_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("transaction.id"), primary_key=True
        ),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_role", sa.String(), nullable=True),
        sa.Column("object_type", sa.String(), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_state", sa.String(), nullable=True),
        sa.Column("to_state", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_correlation_id", "audit_log", ["correlation_id"])
    op.create_index("ix_audit_log_object_type", "audit_log", ["object_type"])
    op.create_index("ix_audit_log_object_id", "audit_log", ["object_id"])

    op.create_table(
        "quarantine_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("source_file_name", sa.String(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw_row", postgresql.JSONB(), nullable=False),
        sa.Column("error_reason", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("quarantine_item")
    op.drop_table("audit_log")
    op.drop_table("alert_transaction")
    op.drop_table("alert")
    op.drop_table("case")
    op.drop_table("transaction")
    op.drop_table("account")
    op.drop_table("customer")
    op.drop_table("sessions")
    op.drop_table("users")

    sa.Enum(name="alert_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="case_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="transaction_direction").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="account_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="entity_type").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
