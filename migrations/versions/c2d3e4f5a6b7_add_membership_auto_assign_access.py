"""add membership auto assign access

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-04-10 00:00:01.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedule_user_department_membership",
        sa.Column(
            "can_auto_assign",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE schedule_user_department_membership
            SET can_auto_assign = TRUE
            WHERE lower(trim(role)) IN ('manager', 'gm')
            """
        )
    )


def downgrade() -> None:
    op.drop_column("schedule_user_department_membership", "can_auto_assign")
