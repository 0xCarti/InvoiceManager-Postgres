"""expand activity_log.activity to text

Revision ID: e3b7c9a1f4d2
Revises: d2f7a1b9c8e0
Create Date: 2026-03-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e3b7c9a1f4d2"
down_revision = "d2f7a1b9c8e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE activity_log ALTER COLUMN activity TYPE TEXT"
        )
    else:
        op.alter_column(
            "activity_log",
            "activity",
            existing_type=sa.String(length=255),
            type_=sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Truncate over-length values before shrinking the column type.
        op.execute(
            "UPDATE activity_log SET activity = LEFT(activity, 255) "
            "WHERE length(activity) > 255"
        )
        op.execute(
            "ALTER TABLE activity_log ALTER COLUMN activity TYPE VARCHAR(255)"
        )
    else:
        op.alter_column(
            "activity_log",
            "activity",
            existing_type=sa.Text(),
            type_=sa.String(length=255),
            existing_nullable=False,
        )
