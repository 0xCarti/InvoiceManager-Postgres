"""add permission system

Revision ID: d9e0f1a2b3c4
Revises: c6d7e8f9a0b1
Create Date: 2026-04-03 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d9e0f1a2b3c4"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "permission",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index(
        "ix_permission_category_code",
        "permission",
        ["category", "code"],
        unique=False,
    )

    op.create_table(
        "permission_group",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_system", sa.Boolean(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        "ix_permission_group_is_system",
        "permission_group",
        ["is_system"],
        unique=False,
    )

    op.create_table(
        "permission_group_permissions",
        sa.Column("permission_group_id", sa.Integer(), nullable=False),
        sa.Column("permission_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permission_group_id"], ["permission_group.id"]),
        sa.ForeignKeyConstraint(["permission_id"], ["permission.id"]),
        sa.PrimaryKeyConstraint("permission_group_id", "permission_id"),
    )

    op.create_table(
        "user_permission_groups",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("permission_group_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["permission_group_id"], ["permission_group.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("user_id", "permission_group_id"),
    )


def downgrade() -> None:
    op.drop_table("user_permission_groups")
    op.drop_table("permission_group_permissions")
    op.drop_index("ix_permission_group_is_system", table_name="permission_group")
    op.drop_table("permission_group")
    op.drop_index("ix_permission_category_code", table_name="permission")
    op.drop_table("permission")
