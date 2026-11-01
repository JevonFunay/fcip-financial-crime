"""Merge the two 0010 migrations into one head.

Revision ID: 0011_merge_lab_and_roles
Revises: 0010_model_lab, 0010_roles_case_evidence
Create Date: 2026-10-27

0010_roles_case_evidence (roles, case lifecycle, evidence) and 0010_model_lab
(model lab tables) were written in parallel on top of 0009, leaving two heads,
so `alembic upgrade head` refused to run. They touch different tables and
apply cleanly in either order; this revision only joins them. A database that
already applied one, the other or both upgrades normally.
"""

revision = "0011_merge_lab_and_roles"
down_revision = ("0010_model_lab", "0010_roles_case_evidence")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
