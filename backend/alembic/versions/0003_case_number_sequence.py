"""add case_number sequence

Revision ID: 0003_case_number_sequence
Revises: 0002_alert_correlation_id
Create Date: 2026-09-18

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_case_number_sequence"
down_revision: Union[str, None] = "0002_alert_correlation_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Human-readable, gap-tolerant case numbers (CASE-000001, ...) without a
    # read-then-insert race between two investigators opening cases at once.
    op.execute("CREATE SEQUENCE IF NOT EXISTS case_number_seq START 1")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS case_number_seq")
