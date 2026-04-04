"""add invoice status workflow

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-04-03 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e1f2a3b4c5d6"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invoice",
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("invoice", sa.Column("delivered_at", sa.DateTime(), nullable=True))

    op.execute(
        """
        UPDATE invoice
        SET status = CASE
            WHEN is_paid THEN 'paid'
            ELSE 'delivered'
        END
        """
    )
    op.execute(
        """
        UPDATE invoice
        SET delivered_at = COALESCE(delivered_at, paid_at, date_created)
        WHERE status IN ('delivered', 'paid')
        """
    )

    op.create_check_constraint(
        "ck_invoice_status",
        "invoice",
        "status IN ('pending', 'delivered', 'paid')",
    )
    op.create_index("ix_invoice_status", "invoice", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invoice_status", table_name="invoice")
    op.drop_constraint("ck_invoice_status", "invoice", type_="check")
    op.drop_column("invoice", "delivered_at")
    op.drop_column("invoice", "status")
