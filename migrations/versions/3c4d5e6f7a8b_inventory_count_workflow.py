"""inventory count workflow

Revision ID: 3c4d5e6f7a8b
Revises: 2b3c4d5e6f7a
Create Date: 2026-07-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "3c4d5e6f7a8b"
down_revision = "2b3c4d5e6f7a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "location_stand_item",
        sa.Column(
            "active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "location_stand_item",
        sa.Column(
            "recipe_backed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "location_count_submission_row",
        sa.Column("unit_breakdown", sa.JSON(), nullable=True),
    )

    op.drop_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        type_="check",
    )
    op.create_check_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        "submission_type IN ('opening', 'closing', 'eaten', 'spoilage', 'inventory')",
    )


def downgrade():
    op.drop_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        type_="check",
    )
    op.create_check_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        "submission_type IN ('opening', 'closing', 'eaten', 'spoilage')",
    )

    op.drop_column("location_count_submission_row", "unit_breakdown")
    op.drop_column("location_stand_item", "recipe_backed")
    op.drop_column("location_stand_item", "active")
