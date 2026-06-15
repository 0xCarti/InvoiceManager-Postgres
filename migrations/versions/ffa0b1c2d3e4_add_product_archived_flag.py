"""add product archived flag

Revision ID: ffa0b1c2d3e4
Revises: ff9a0b1c2d3
Create Date: 2026-06-06 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "ffa0b1c2d3e4"
down_revision = "ff9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "product",
        sa.Column(
            "archived",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade():
    op.drop_column("product", "archived")
