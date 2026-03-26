"""Add payment fields to invoice.

Revision ID: 202603210001
Revises: 282aed628bb1
Create Date: 2026-03-21 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "202603210001"
down_revision = "282aed628bb1"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("invoice", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_paid",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(sa.Column("paid_at", sa.DateTime(), nullable=True))

    invoice = sa.table(
        "invoice",
        sa.column("is_paid", sa.Boolean()),
        sa.column("paid_at", sa.DateTime()),
    )
    op.execute(
        invoice.update().values(is_paid=sa.false(), paid_at=None)
    )


def downgrade():
    with op.batch_alter_table("invoice", recreate="always") as batch_op:
        batch_op.drop_column("paid_at")
        batch_op.drop_column("is_paid")
