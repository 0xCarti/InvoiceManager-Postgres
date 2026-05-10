"""add location count submission approval mode

Revision ID: c8e9f0a1b2c3
Revises: b2c3d4e5f6a7
Create Date: 2026-05-10 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c8e9f0a1b2c3"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "location_count_submission",
        sa.Column(
            "approval_mode",
            sa.String(length=16),
            nullable=False,
            server_default="add",
        ),
    )
    op.create_check_constraint(
        "ck_location_count_submission_approval_mode",
        "location_count_submission",
        "approval_mode IN ('add', 'overwrite')",
    )


def downgrade():
    op.drop_constraint(
        "ck_location_count_submission_approval_mode",
        "location_count_submission",
        type_="check",
    )
    op.drop_column("location_count_submission", "approval_mode")
