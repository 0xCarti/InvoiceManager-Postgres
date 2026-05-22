"""add user default transfer from location

Revision ID: fc6d7e8f9a0
Revises: fb5c6d7e8f9
Create Date: 2026-05-21 23:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fc6d7e8f9a0"
down_revision = "fb5c6d7e8f9"
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
