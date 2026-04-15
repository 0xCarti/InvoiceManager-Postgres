"""add signage media assets and block grid positioning

Revision ID: f8a9b0c1d2e3
Revises: f6a7b8c9d0e1
Create Date: 2026-04-14 19:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f8a9b0c1d2e3"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "signage_media_asset",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column(
            "file_size_bytes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("uploaded_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "media_type IN ('image', 'video')",
            name="ck_signage_media_asset_media_type",
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_signage_media_asset_media_type",
        "signage_media_asset",
        ["media_type"],
        unique=False,
    )
    op.create_index(
        "ix_signage_media_asset_uploaded_by",
        "signage_media_asset",
        ["uploaded_by"],
        unique=False,
    )
    op.create_index(
        "ix_signage_media_asset_sha256",
        "signage_media_asset",
        ["sha256"],
        unique=False,
    )

    with op.batch_alter_table("signage_board_template_block") as batch_op:
        batch_op.add_column(sa.Column("media_asset_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("grid_x", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("grid_y", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(
            sa.Column("grid_width", sa.Integer(), nullable=False, server_default="12")
        )
        batch_op.add_column(
            sa.Column("grid_height", sa.Integer(), nullable=False, server_default="10")
        )
        batch_op.create_foreign_key(
            "fk_signage_board_template_block_media_asset_id",
            "signage_media_asset",
            ["media_asset_id"],
            ["id"],
        )
        batch_op.create_index(
            "ix_signage_board_template_block_media_asset_id",
            ["media_asset_id"],
            unique=False,
        )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, board_template_id, position, width_units
            FROM signage_board_template_block
            ORDER BY board_template_id, position, id
            """
        )
    ).mappings()

    current_template_id = None
    current_x = 1
    current_y = 1
    row_height = 10
    for row in rows:
        template_id = row["board_template_id"]
        width_units = int(row["width_units"] or 6)
        grid_width = max(4, min(width_units * 2, 24))
        grid_height = 10

        if current_template_id != template_id:
            current_template_id = template_id
            current_x = 1
            current_y = 1
            row_height = grid_height

        if current_x + grid_width - 1 > 24:
            current_x = 1
            current_y = min(current_y + row_height, 12)
            row_height = grid_height

        safe_y = min(current_y, max(12 - grid_height + 1, 1))
        connection.execute(
            sa.text(
                """
                UPDATE signage_board_template_block
                SET media_asset_id = NULL,
                    grid_x = :grid_x,
                    grid_y = :grid_y,
                    grid_width = :grid_width,
                    grid_height = :grid_height
                WHERE id = :block_id
                """
            ),
            {
                "block_id": row["id"],
                "grid_x": current_x,
                "grid_y": safe_y,
                "grid_width": grid_width,
                "grid_height": grid_height,
            },
        )
        current_x += grid_width
        row_height = max(row_height, grid_height)

    with op.batch_alter_table("signage_board_template_block") as batch_op:
        batch_op.alter_column("grid_x", server_default=None)
        batch_op.alter_column("grid_y", server_default=None)
        batch_op.alter_column("grid_width", server_default=None)
        batch_op.alter_column("grid_height", server_default=None)


def downgrade():
    with op.batch_alter_table("signage_board_template_block") as batch_op:
        batch_op.drop_index("ix_signage_board_template_block_media_asset_id")
        batch_op.drop_constraint(
            "fk_signage_board_template_block_media_asset_id",
            type_="foreignkey",
        )
        batch_op.drop_column("grid_height")
        batch_op.drop_column("grid_width")
        batch_op.drop_column("grid_y")
        batch_op.drop_column("grid_x")
        batch_op.drop_column("media_asset_id")

    op.drop_index("ix_signage_media_asset_sha256", table_name="signage_media_asset")
    op.drop_index("ix_signage_media_asset_uploaded_by", table_name="signage_media_asset")
    op.drop_index("ix_signage_media_asset_media_type", table_name="signage_media_asset")
    op.drop_table("signage_media_asset")
