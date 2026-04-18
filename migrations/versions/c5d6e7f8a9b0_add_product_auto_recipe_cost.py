"""add product auto recipe cost flag

Revision ID: c5d6e7f8a9b0
Revises: c3d4e5f6a7b8
Create Date: 2026-04-17 21:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("product") as batch_op:
        batch_op.add_column(
            sa.Column(
                "auto_update_recipe_cost",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("product") as batch_op:
        batch_op.drop_column("auto_update_recipe_cost")
