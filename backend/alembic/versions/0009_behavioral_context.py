"""Observed behavioral metadata; unknown source facts remain nullable.

Revision ID: 0009_behavioral_context
Revises: 0008_ml_scoring
Create Date: 2026-10-14

No currency conversion, inferred customer identity or fabricated timezone is
introduced. Existing records are left null, not backfilled with invented facts.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_behavioral_context"
down_revision = "0008_ml_scoring"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name, kind in (
        ("location", sa.String()),
        ("ip_address", sa.String()),
        ("login_attempts", sa.Integer()),
        ("transaction_duration", sa.Float()),
        ("account_balance", sa.Float()),
        ("customer_age", sa.Float()),
        ("customer_occupation", sa.String()),
        ("timestamp_precision", sa.String(6)),
        ("source_timezone_known", sa.Boolean()),
    ):
        op.add_column("transaction", sa.Column(name, kind, nullable=True))


def downgrade() -> None:
    for name in (
        "source_timezone_known", "timestamp_precision", "customer_occupation",
        "customer_age", "account_balance", "transaction_duration", "login_attempts",
        "ip_address", "location",
    ):
        op.drop_column("transaction", name)
