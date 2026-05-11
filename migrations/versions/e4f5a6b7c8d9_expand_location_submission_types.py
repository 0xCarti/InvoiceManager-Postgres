"""expand location submission types

Revision ID: e4f5a6b7c8d9
Revises: d0aa1bb2cc3d
Create Date: 2026-05-10 00:00:01.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "e4f5a6b7c8d9"
down_revision = "d0aa1bb2cc3d"
branch_labels = None
depends_on = None


def upgrade():
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


def downgrade():
    op.drop_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        type_="check",
    )
    op.create_check_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        "submission_type IN ('opening', 'closing')",
    )
