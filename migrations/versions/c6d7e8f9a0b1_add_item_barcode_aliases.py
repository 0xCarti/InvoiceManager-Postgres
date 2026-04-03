"""add item barcode aliases

Revision ID: c6d7e8f9a0b1
Revises: b5d6e7f8a9c0
Create Date: 2026-04-03 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c6d7e8f9a0b1"
down_revision = "b5d6e7f8a9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_barcode",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_item_barcode_code", "item_barcode", ["code"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_item_barcode_code", table_name="item_barcode")
    op.drop_table("item_barcode")
