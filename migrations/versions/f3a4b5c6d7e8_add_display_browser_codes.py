"""add display browser codes

Revision ID: f3a4b5c6d7e8
Revises: f2a3b4c5d6e7
Create Date: 2026-04-14 16:20:00.000000

"""

import secrets

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f3a4b5c6d7e8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

_display_table = sa.table(
    "signage_display",
    sa.column("id", sa.Integer()),
    sa.column("browser_code", sa.String(length=8)),
)


def _generate_browser_code(connection) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        existing_id = connection.execute(
            sa.select(_display_table.c.id).where(_display_table.c.browser_code == code)
        ).scalar()
        if existing_id is None:
            return code


def upgrade() -> None:
    with op.batch_alter_table("signage_display") as batch_op:
        batch_op.add_column(sa.Column("browser_code", sa.String(length=8), nullable=True))

    connection = op.get_bind()
    display_ids = connection.execute(sa.select(_display_table.c.id)).scalars().all()
    for display_id in display_ids:
        connection.execute(
            _display_table.update()
            .where(_display_table.c.id == display_id)
            .values(browser_code=_generate_browser_code(connection))
        )

    with op.batch_alter_table("signage_display") as batch_op:
        batch_op.alter_column(
            "browser_code",
            existing_type=sa.String(length=8),
            nullable=False,
        )
        batch_op.create_index(
            "ix_signage_display_browser_code",
            ["browser_code"],
            unique=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("signage_display") as batch_op:
        batch_op.drop_index("ix_signage_display_browser_code")
        batch_op.drop_column("browser_code")
