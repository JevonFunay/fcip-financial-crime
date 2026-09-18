"""add alert.correlation_id

Revision ID: 0002_alert_correlation_id
Revises: 0001_initial_schema
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_alert_correlation_id"
down_revision: Union[str, None] = "0001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Temporary server default backfills any existing rows; dropped right after
    # so the application is always the one assigning correlation ids.
    op.add_column(
        "alert",
        sa.Column(
            "correlation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.alter_column("alert", "correlation_id", server_default=None)
    op.create_index("ix_alert_correlation_id", "alert", ["correlation_id"])


def downgrade() -> None:
    op.drop_index("ix_alert_correlation_id", table_name="alert")
    op.drop_column("alert", "correlation_id")
