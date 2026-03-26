"""add estimated sales to event

Revision ID: 1f2e3d4c5b6a
Revises: c4d5e6f7a8b9
Create Date: 2025-10-15 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


def _has_column(table_name: str, column_name: str, bind) -> bool:
    """Return True if the given table already has the specified column."""

    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name)
    }


# revision identifiers, used by Alembic.
revision = "1f2e3d4c5b6a"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table = "event"
    column = "estimated_sales"

    if _has_column(table, column, bind):
        return

    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.add_column(sa.Column(column, sa.Numeric(12, 2), nullable=True))


def downgrade():
    bind = op.get_bind()
    table = "event"
    column = "estimated_sales"

    if not _has_column(table, column, bind):
        return

    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.drop_column(column)
