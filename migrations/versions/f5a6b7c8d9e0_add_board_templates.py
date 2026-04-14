"""add board templates

Revision ID: f5a6b7c8d9e0
Revises: f4a5b6c7d8e9
Create Date: 2026-04-14 23:05:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f5a6b7c8d9e0"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signage_board_template",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("theme", sa.String(length=32), nullable=False, server_default="aurora"),
        sa.Column("canvas_width", sa.Integer(), nullable=False, server_default="1920"),
        sa.Column("canvas_height", sa.Integer(), nullable=False, server_default="1080"),
        sa.Column("menu_columns", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("menu_rows", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("show_prices", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column(
            "show_menu_description",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "show_page_indicator",
            sa.Boolean(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("brand_label", sa.String(length=80), nullable=True),
        sa.Column("brand_name", sa.String(length=120), nullable=True),
        sa.Column(
            "side_panel_position",
            sa.String(length=16),
            nullable=False,
            server_default="none",
        ),
        sa.Column(
            "side_panel_width_percent",
            sa.Integer(),
            nullable=False,
            server_default="30",
        ),
        sa.Column("side_title", sa.String(length=120), nullable=True),
        sa.Column("side_body", sa.Text(), nullable=True),
        sa.Column("side_image_url", sa.Text(), nullable=True),
        sa.Column("footer_text", sa.String(length=255), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        "ix_signage_board_template_archived",
        "signage_board_template",
        ["archived"],
        unique=False,
    )

    with op.batch_alter_table("signage_display") as batch_op:
        batch_op.add_column(
            sa.Column("board_template_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_signage_display_board_template_id",
            "signage_board_template",
            ["board_template_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_signage_display_board_template_id",
            ["board_template_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("signage_display") as batch_op:
        batch_op.drop_index("ix_signage_display_board_template_id")
        batch_op.drop_constraint(
            "fk_signage_display_board_template_id",
            type_="foreignkey",
        )
        batch_op.drop_column("board_template_id")

    op.drop_index(
        "ix_signage_board_template_archived",
        table_name="signage_board_template",
    )
    op.drop_table("signage_board_template")
