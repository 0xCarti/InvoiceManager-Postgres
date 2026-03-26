"""add reversal reason to pos sales import

Revision ID: 202603260004
Revises: 202603260003
Create Date: 2026-03-26 00:04:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202603260004"
down_revision = "202603260003"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pos_sales_import",
        sa.Column("reversal_reason", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("pos_sales_import", "reversal_reason")
