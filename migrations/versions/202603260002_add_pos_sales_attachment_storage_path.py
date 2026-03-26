"""Add storage path column for POS sales import attachments.

Revision ID: 202603260002
Revises: 202603260001
Create Date: 2026-03-26 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "202603260002"
down_revision = "202603260001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pos_sales_import",
        sa.Column("attachment_storage_path", sa.String(length=1024), nullable=True),
    )


def downgrade():
    op.drop_column("pos_sales_import", "attachment_storage_path")
