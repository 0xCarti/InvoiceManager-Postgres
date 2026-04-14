"""add display board settings

Revision ID: f4a5b6c7d8e9
Revises: f3a4b5c6d7e8
Create Date: 2026-04-14 21:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f4a5b6c7d8e9"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("signage_display") as batch_op:
        batch_op.add_column(
            sa.Column("board_columns", sa.Integer(), nullable=False, server_default="3")
        )
        batch_op.add_column(
            sa.Column("board_rows", sa.Integer(), nullable=False, server_default="4")
        )
        batch_op.add_column(
            sa.Column("show_prices", sa.Boolean(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column(
                "show_menu_description",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("selected_product_ids", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("signage_display") as batch_op:
        batch_op.drop_column("selected_product_ids")
        batch_op.drop_column("show_menu_description")
        batch_op.drop_column("show_prices")
        batch_op.drop_column("board_rows")
        batch_op.drop_column("board_columns")
