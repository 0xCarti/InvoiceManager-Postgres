"""Add explicit user invitation state.

Revision ID: 7b8c9d0e1f2a
Revises: 6f7a8b9c0d1e
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "7b8c9d0e1f2a"
down_revision = "6f7a8b9c0d1e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column(
            "invitation_pending",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Preserve the pre-migration meaning used throughout the application.
    # Once migrated, all future state transitions use this explicit flag.
    op.execute(
        sa.text(
            'UPDATE "user" '
            "SET invitation_pending = true "
            "WHERE active = false AND last_login_at IS NULL"
        )
    )


def downgrade():
    op.drop_column("user", "invitation_pending")
