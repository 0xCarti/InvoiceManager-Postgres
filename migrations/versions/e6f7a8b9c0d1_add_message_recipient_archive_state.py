"""add message recipient archive state

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-04-12 17:45:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "communication_recipient",
        sa.Column("archived_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "communication_recipient",
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_communication_recipient_user_archived",
        "communication_recipient",
        ["user_id", "archived_at"],
        unique=False,
    )
    op.create_index(
        "ix_communication_recipient_user_deleted",
        "communication_recipient",
        ["user_id", "deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_communication_recipient_user_deleted",
        table_name="communication_recipient",
    )
    op.drop_index(
        "ix_communication_recipient_user_archived",
        table_name="communication_recipient",
    )
    op.drop_column("communication_recipient", "deleted_at")
    op.drop_column("communication_recipient", "archived_at")
