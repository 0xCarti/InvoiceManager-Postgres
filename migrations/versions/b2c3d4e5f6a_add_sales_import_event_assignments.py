"""add sales import event assignments

Revision ID: b2c3d4e5f6a
Revises: a1b2c3d4e5f6
Create Date: 2026-04-16 12:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("pos_sales_import") as batch_op:
        batch_op.add_column(sa.Column("sales_date", sa.Date(), nullable=True))
        batch_op.create_index("ix_pos_sales_import_sales_date", ["sales_date"], unique=False)

    with op.batch_alter_table("pos_sales_import_location") as batch_op:
        batch_op.add_column(sa.Column("event_location_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("approval_metadata", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_pos_sales_import_location_event_location_id",
            "event_location",
            ["event_location_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_pos_sales_import_location_event_location_id",
            ["event_location_id"],
            unique=False,
        )


def downgrade():
    with op.batch_alter_table("pos_sales_import_location") as batch_op:
        batch_op.drop_index("ix_pos_sales_import_location_event_location_id")
        batch_op.drop_constraint(
            "fk_pos_sales_import_location_event_location_id",
            type_="foreignkey",
        )
        batch_op.drop_column("approval_metadata")
        batch_op.drop_column("event_location_id")

    with op.batch_alter_table("pos_sales_import") as batch_op:
        batch_op.drop_index("ix_pos_sales_import_sales_date")
        batch_op.drop_column("sales_date")
