"""Lab datasets can be CSV or Parquet.

Revision ID: 0012_lab_file_format
Revises: 0011_merge_lab_and_roles
Create Date: 2026-10-27

Mentor request (27 Oct 2026): uploads accept Parquet as well as CSV. The
stored original file keeps its bytes unchanged, so the column that holds it is
renamed from raw_csv to raw_file, and file_format records which reader opens
it. Existing rows were all CSV.
"""

from alembic import op
import sqlalchemy as sa

revision = "0012_lab_file_format"
down_revision = "0011_merge_lab_and_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("lab_dataset", "raw_csv", new_column_name="raw_file")
    op.add_column("lab_dataset", sa.Column("file_format", sa.String(), nullable=False, server_default="csv"))
    op.create_check_constraint("ck_lab_dataset_file_format", "lab_dataset", "file_format IN ('csv', 'parquet')")


def downgrade() -> None:
    op.drop_constraint("ck_lab_dataset_file_format", "lab_dataset", type_="check")
    op.drop_column("lab_dataset", "file_format")
    op.alter_column("lab_dataset", "raw_file", new_column_name="raw_csv")
