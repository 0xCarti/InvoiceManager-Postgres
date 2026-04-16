"""add purchase order workflow status

Revision ID: a1b2c3d4e5f6
Revises: f9a0b1c2d3e4
Create Date: 2026-04-15 22:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("purchase_order") as batch_op:
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="requested",
            )
        )
        batch_op.create_check_constraint(
            "ck_purchase_order_status",
            "status IN ('requested', 'ordered', 'received')",
        )
        batch_op.create_index("ix_purchase_order_status", ["status"], unique=False)

    op.execute("UPDATE purchase_order SET status = 'received' WHERE received = 1")


def downgrade():
    with op.batch_alter_table("purchase_order") as batch_op:
        batch_op.drop_index("ix_purchase_order_status")
        batch_op.drop_constraint("ck_purchase_order_status", type_="check")
        batch_op.drop_column("status")
