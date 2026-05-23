"""add event operating day confirmation

Revision ID: 0a1b2c3d4e5f
Revises: ff9a0b1c2d3
Create Date: 2026-05-22 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "0a1b2c3d4e5f"
down_revision = "ff9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "event_location_operating_day",
        sa.Column(
            "confirmed",
            sa.Boolean(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "event_location_operating_day",
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "event_location_operating_day",
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_event_location_operating_day_confirmed_by_user",
        "event_location_operating_day",
        "user",
        ["confirmed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    event_location = sa.table(
        "event_location",
        sa.column("id", sa.Integer),
        sa.column("confirmed", sa.Boolean),
    )
    operating_day = sa.table(
        "event_location_operating_day",
        sa.column("event_location_id", sa.Integer),
        sa.column("confirmed", sa.Boolean),
        sa.column("confirmed_at", sa.DateTime),
    )
    bind = op.get_bind()
    confirmed_location_ids = sa.select(event_location.c.id).where(
        event_location.c.confirmed.is_(True)
    )
    bind.execute(
        operating_day.update()
        .where(operating_day.c.event_location_id.in_(confirmed_location_ids))
        .values(confirmed=True, confirmed_at=sa.func.now())
    )


def downgrade():
    op.drop_constraint(
        "fk_event_location_operating_day_confirmed_by_user",
        "event_location_operating_day",
        type_="foreignkey",
    )
    op.drop_column("event_location_operating_day", "confirmed_by_user_id")
    op.drop_column("event_location_operating_day", "confirmed_at")
    op.drop_column("event_location_operating_day", "confirmed")
