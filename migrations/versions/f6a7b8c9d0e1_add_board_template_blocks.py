"""add board template blocks

Revision ID: f6a7b8c9d0e1
Revises: f5a6b7c8d9e0
Create Date: 2026-04-15 00:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signage_board_template_block",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("board_template_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("block_type", sa.String(length=32), nullable=False, server_default="menu"),
        sa.Column("width_units", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("media_url", sa.Text(), nullable=True),
        sa.Column("menu_columns", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("menu_rows", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("show_title", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("show_prices", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "show_menu_description",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("selected_product_ids", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["board_template_id"],
            ["signage_board_template.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_signage_board_template_block_template_position",
        "signage_board_template_block",
        ["board_template_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_signage_board_template_block_template_position",
        table_name="signage_board_template_block",
    )
    op.drop_table("signage_board_template_block")
