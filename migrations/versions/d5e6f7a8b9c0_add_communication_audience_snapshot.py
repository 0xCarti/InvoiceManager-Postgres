"""add communication audience snapshot

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-04-11 17:35:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "communication",
        sa.Column("audience_snapshot", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("communication", "audience_snapshot")
