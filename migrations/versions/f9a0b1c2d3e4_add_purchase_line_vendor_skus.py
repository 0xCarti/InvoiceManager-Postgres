"""add vendor sku tracking to purchase lines

Revision ID: f9a0b1c2d3e4
Revises: f8a9b0c1d2e3
Create Date: 2026-04-15 10:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f9a0b1c2d3e4"
down_revision = "f8a9b0c1d2e3"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_order_item") as batch_op:
        batch_op.add_column(sa.Column("vendor_sku", sa.String(length=100), nullable=True))

    with op.batch_alter_table("purchase_invoice_item") as batch_op:
        batch_op.add_column(sa.Column("vendor_sku", sa.String(length=100), nullable=True))


def downgrade():
    with op.batch_alter_table("purchase_invoice_item") as batch_op:
        batch_op.drop_column("vendor_sku")

    with op.batch_alter_table("purchase_order_item") as batch_op:
        batch_op.drop_column("vendor_sku")
