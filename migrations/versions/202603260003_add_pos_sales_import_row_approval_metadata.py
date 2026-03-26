"""Add approval metadata to POS sales import rows.

Revision ID: 202603260003
Revises: 202603260002
Create Date: 2026-03-26 01:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "202603260003"
down_revision = "202603260002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pos_sales_import_row",
        sa.Column("approval_metadata", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("pos_sales_import_row", "approval_metadata")
