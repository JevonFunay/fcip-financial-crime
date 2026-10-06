"""the transaction context detection needs: type, country, merchant, device

Revision ID: 0007_transaction_context
Revises: 0006_seed_p02_rule
Create Date: 2026-10-06

The ingestion contract carried only what P02 needed. The models also read the
transaction type, the counterparty's country, the merchant and the device, and
the source's own status (REVERSED), all part of TRD §6.1's transactions.csv;
the bridge dropped them until now. Merchants and devices are master data, like
customers and accounts.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_transaction_context"
down_revision: Union[str, None] = "0006_seed_p02_rule"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for column in (
        sa.Column("transaction_type", sa.String(), nullable=True),
        sa.Column("counterparty_country", sa.String(length=2), nullable=True),
        sa.Column("merchant_ref", sa.String(), nullable=True),
        sa.Column("device_ref", sa.String(), nullable=True),
        sa.Column("source_status", sa.String(), nullable=True),
    ):
        op.add_column("transaction", column)
    op.create_index(op.f("ix_transaction_merchant_ref"), "transaction", ["merchant_ref"])
    op.create_index(op.f("ix_transaction_device_ref"), "transaction", ["device_ref"])

    op.create_table(
        "merchant",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("merchant_ref", sa.String(), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("mcc", sa.String(length=4), nullable=False),
        sa.Column("declared_ticket_band", sa.String(), nullable=True),
        sa.Column("operating_hours", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customer.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_merchant_merchant_ref"), "merchant", ["merchant_ref"], unique=True)
    op.create_index(op.f("ix_merchant_customer_id"), "merchant", ["customer_id"])

    op.create_table(
        "device",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("device_ref", sa.String(), nullable=False),
        sa.Column("device_type", sa.String(), nullable=True),
        sa.Column("is_emulator", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_rooted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_device_device_ref"), "device", ["device_ref"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_device_device_ref"), table_name="device")
    op.drop_table("device")
    op.drop_index(op.f("ix_merchant_customer_id"), table_name="merchant")
    op.drop_index(op.f("ix_merchant_merchant_ref"), table_name="merchant")
    op.drop_table("merchant")
    op.drop_index(op.f("ix_transaction_device_ref"), table_name="transaction")
    op.drop_index(op.f("ix_transaction_merchant_ref"), table_name="transaction")
    for column in ("source_status", "device_ref", "merchant_ref", "counterparty_country", "transaction_type"):
        op.drop_column("transaction", column)
