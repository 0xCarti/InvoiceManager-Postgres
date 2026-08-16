"""Add paired transfer location submissions.

Revision ID: 9d0e1f2a3b4c
Revises: 8c9d0e1f2a3b
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op


revision = "9d0e1f2a3b4c"
down_revision = "8c9d0e1f2a3b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "event_stand_sheet_item",
        sa.Column(
            "reported_transferred_in",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
    )
    op.add_column(
        "event_stand_sheet_item",
        sa.Column(
            "reported_transferred_out",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
    )
    op.add_column(
        "location_count_submission_row",
        sa.Column(
            "transfer_in_value",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
    )
    op.add_column(
        "location_count_submission_row",
        sa.Column(
            "transfer_out_value",
            sa.Float(),
            nullable=False,
            server_default="0.0",
        ),
    )
    op.create_check_constraint(
        "ck_location_count_submission_row_transfer_in_nonnegative",
        "location_count_submission_row",
        "transfer_in_value >= 0",
    )
    op.create_check_constraint(
        "ck_location_count_submission_row_transfer_out_nonnegative",
        "location_count_submission_row",
        "transfer_out_value >= 0",
    )
    op.drop_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        type_="check",
    )
    op.create_check_constraint(
        "ck_location_count_submission_type",
        "location_count_submission",
        "submission_type IN ('opening', 'closing', 'eaten', 'spoilage', 'transfer', 'inventory')",
    )


def downgrade():
    # The prior schema cannot represent paired transfer submissions. Remove
    # those parent rows (and their ON DELETE CASCADE children) before restoring
    # the old submission-type constraint so the downgrade remains executable.
    op.execute(
        "DELETE FROM location_count_submission WHERE submission_type = 'transfer'"
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
    op.drop_constraint(
        "ck_location_count_submission_row_transfer_out_nonnegative",
        "location_count_submission_row",
        type_="check",
    )
    op.drop_constraint(
        "ck_location_count_submission_row_transfer_in_nonnegative",
        "location_count_submission_row",
        type_="check",
    )
    op.drop_column("location_count_submission_row", "transfer_out_value")
    op.drop_column("location_count_submission_row", "transfer_in_value")
    op.drop_column("event_stand_sheet_item", "reported_transferred_out")
    op.drop_column("event_stand_sheet_item", "reported_transferred_in")
