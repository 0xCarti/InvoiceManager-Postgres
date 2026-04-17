"""add ignored status to pos sales import

Revision ID: c3d4e5f6a7b8
Revises: b3c4d5e6f7a
Create Date: 2026-04-17 18:10:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("pos_sales_import") as batch_op:
        batch_op.drop_constraint("ck_pos_sales_import_status", type_="check")
        batch_op.create_check_constraint(
            "ck_pos_sales_import_status",
            "status IN ('pending', 'needs_mapping', 'approved', 'reversed', 'deleted', 'failed', 'ignored')",
        )


def downgrade():
    with op.batch_alter_table("pos_sales_import") as batch_op:
        batch_op.drop_constraint("ck_pos_sales_import_status", type_="check")
        batch_op.create_check_constraint(
            "ck_pos_sales_import_status",
            "status IN ('pending', 'needs_mapping', 'approved', 'reversed', 'deleted', 'failed')",
        )
