"""add operational notification preferences

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-05-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c7d8e9f0a1b2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column(
            "notify_transfers_email",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_purchase_orders_email",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_purchase_orders_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_events_email",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_events_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_users_email",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_users_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_messages_email",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_messages_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_bulletins_email",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_bulletins_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_locations_email",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "user",
        sa.Column(
            "notify_locations_text",
            sa.Boolean(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade():
    op.drop_column("user", "notify_locations_text")
    op.drop_column("user", "notify_locations_email")
    op.drop_column("user", "notify_bulletins_text")
    op.drop_column("user", "notify_bulletins_email")
    op.drop_column("user", "notify_messages_text")
    op.drop_column("user", "notify_messages_email")
    op.drop_column("user", "notify_users_text")
    op.drop_column("user", "notify_users_email")
    op.drop_column("user", "notify_events_text")
    op.drop_column("user", "notify_events_email")
    op.drop_column("user", "notify_purchase_orders_text")
    op.drop_column("user", "notify_purchase_orders_email")
    op.drop_column("user", "notify_transfers_email")
