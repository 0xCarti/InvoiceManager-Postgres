"""add user default transfer from location

Revision ID: fc6d7e8f9a0
Revises: f9a0b1c2d3e4
Create Date: 2026-05-21 23:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fc6d7e8f9a0"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("default_transfer_from_location_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_default_transfer_from_location_id_location",
        "user",
        "location",
        ["default_transfer_from_location_id"],
        ["id"],
    )


def downgrade():
    op.drop_constraint(
        "fk_user_default_transfer_from_location_id_location",
        "user",
        type_="foreignkey",
    )
    op.drop_column("user", "default_transfer_from_location_id")
