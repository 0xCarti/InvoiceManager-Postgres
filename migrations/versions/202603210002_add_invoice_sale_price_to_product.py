"""Add dedicated invoice sale price to product.

Revision ID: 202603210002
Revises: 202603210001
Create Date: 2026-03-21 00:30:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "202603210002"
down_revision = "202603210001"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("product", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "invoice_sale_price",
                sa.Numeric(10, 2),
                nullable=True,
            )
        )

    op.execute(
        sa.text(
            "UPDATE product "
            "SET invoice_sale_price = COALESCE(invoice_sale_price, price, 0)"
        )
    )

def downgrade():
    with op.batch_alter_table("product", recreate="always") as batch_op:
        batch_op.drop_column("invoice_sale_price")
