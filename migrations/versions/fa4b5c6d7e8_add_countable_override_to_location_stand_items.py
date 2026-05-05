"""add countable override to location stand items

Revision ID: fa4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-05-05 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "fa4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "location_stand_item",
        sa.Column(
            "countable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade():
    op.drop_column("location_stand_item", "countable")
