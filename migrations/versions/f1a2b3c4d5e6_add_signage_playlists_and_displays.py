"""add signage playlists and displays

Revision ID: f1a2b3c4d5e6
Revises: e6f7a8b9c0d1
Create Date: 2026-04-14 12:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playlist",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "playlist_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("playlist_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "source_type",
            sa.String(length=32),
            nullable=False,
            server_default="location_menu",
        ),
        sa.Column("menu_id", sa.Integer(), nullable=True),
        sa.Column(
            "duration_seconds", sa.Integer(), nullable=False, server_default="15"
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["menu_id"], ["menu.id"]),
        sa.ForeignKeyConstraint(["playlist_id"], ["playlist.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_playlist_item_playlist_position",
        "playlist_item",
        ["playlist_id", "position"],
        unique=False,
    )
    op.create_table(
        "signage_display",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column("playlist_override_id", sa.Integer(), nullable=True),
        sa.Column("public_token", sa.String(length=64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("last_seen_ip", sa.String(length=64), nullable=True),
        sa.Column("last_seen_user_agent", sa.String(length=255), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.ForeignKeyConstraint(["playlist_override_id"], ["playlist.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("public_token"),
    )
    op.create_index(
        "ix_signage_display_last_seen_at",
        "signage_display",
        ["last_seen_at"],
        unique=False,
    )
    op.create_index(
        "ix_signage_display_location_archived",
        "signage_display",
        ["location_id", "archived"],
        unique=False,
    )
    op.add_column(
        "location",
        sa.Column("default_playlist_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_location_default_playlist_id_playlist",
        "location",
        "playlist",
        ["default_playlist_id"],
        ["id"],
    )
    op.create_index(
        "ix_location_default_playlist_id",
        "location",
        ["default_playlist_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_location_default_playlist_id", table_name="location")
    op.drop_constraint(
        "fk_location_default_playlist_id_playlist",
        "location",
        type_="foreignkey",
    )
    op.drop_column("location", "default_playlist_id")
    op.drop_index(
        "ix_signage_display_location_archived",
        table_name="signage_display",
    )
    op.drop_index("ix_signage_display_last_seen_at", table_name="signage_display")
    op.drop_table("signage_display")
    op.drop_index("ix_playlist_item_playlist_position", table_name="playlist_item")
    op.drop_table("playlist_item")
    op.drop_table("playlist")
