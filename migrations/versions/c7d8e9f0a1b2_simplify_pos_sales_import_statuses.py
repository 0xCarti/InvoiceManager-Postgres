"""simplify pos sales import statuses

Revision ID: c7d8e9f0a1b2
Revises: b4c5d6e7f8a9
Create Date: 2026-04-26 14:35:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "c7d8e9f0a1b2"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "UPDATE pos_sales_import "
        "SET status = 'pending' "
        "WHERE status IN ('needs_mapping', 'reversed')"
    )
    op.execute(
        "UPDATE pos_sales_import "
        "SET status = 'ignored' "
        "WHERE status = 'failed'"
    )

    with op.batch_alter_table("pos_sales_import") as batch_op:
        batch_op.drop_constraint("ck_pos_sales_import_status", type_="check")
        batch_op.create_check_constraint(
            "ck_pos_sales_import_status",
            "status IN ('pending', 'approved', 'deleted', 'ignored')",
        )


def downgrade():
    op.execute(
        "UPDATE pos_sales_import "
        "SET status = 'failed' "
        "WHERE status = 'ignored' "
        "AND failure_reason = 'Unable to parse POS spreadsheet attachment.'"
    )
    op.execute(
        "UPDATE pos_sales_import "
        "SET status = 'reversed' "
        "WHERE status = 'pending' AND reversed_at IS NOT NULL"
    )

    with op.batch_alter_table("pos_sales_import") as batch_op:
        batch_op.drop_constraint("ck_pos_sales_import_status", type_="check")
        batch_op.create_check_constraint(
            "ck_pos_sales_import_status",
            "status IN ('pending', 'needs_mapping', 'approved', 'reversed', 'deleted', 'failed', 'ignored')",
        )
