"""track pos sales import batches on terminal sale

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-04-24 11:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("terminal_sale") as batch_op:
        batch_op.add_column(
            sa.Column("pos_sales_import_id", sa.Integer(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("approval_batch_id", sa.String(length=64), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_terminal_sale_pos_sales_import_id",
            "pos_sales_import",
            ["pos_sales_import_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_terminal_sale_event_location_batch",
            ["event_location_id", "approval_batch_id"],
            unique=False,
        )
        batch_op.create_index(
            "ix_terminal_sale_pos_sales_import",
            ["pos_sales_import_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("terminal_sale") as batch_op:
        batch_op.drop_index("ix_terminal_sale_pos_sales_import")
        batch_op.drop_index("ix_terminal_sale_event_location_batch")
        batch_op.drop_constraint(
            "fk_terminal_sale_pos_sales_import_id",
            type_="foreignkey",
        )
        batch_op.drop_column("approval_batch_id")
        batch_op.drop_column("pos_sales_import_id")
