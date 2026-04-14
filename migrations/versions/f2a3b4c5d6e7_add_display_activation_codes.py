"""add display activation codes

Revision ID: f2a3b4c5d6e7
Revises: f1a2b3c4d5e6
Create Date: 2026-04-14 14:10:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f2a3b4c5d6e7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "signage_display",
        sa.Column("activation_code", sa.String(length=12), nullable=True),
    )
    op.add_column(
        "signage_display",
        sa.Column("activation_code_expires_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "signage_display",
        sa.Column("last_activated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_signage_display_activation_code",
        "signage_display",
        ["activation_code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_signage_display_activation_code", table_name="signage_display")
    op.drop_column("signage_display", "last_activated_at")
    op.drop_column("signage_display", "activation_code_expires_at")
    op.drop_column("signage_display", "activation_code")
