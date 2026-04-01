"""add invoice_product.is_custom_line

Revision ID: b5d6e7f8a9c0
Revises: a7c9e1d2f3b4
Create Date: 2026-03-31 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b5d6e7f8a9c0"
down_revision = "a7c9e1d2f3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_product", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_custom_line",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("invoice_product", schema=None) as batch_op:
        batch_op.drop_column("is_custom_line")
