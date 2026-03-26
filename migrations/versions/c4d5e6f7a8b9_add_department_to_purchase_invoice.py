"""add department column to purchase_invoice

Revision ID: c4d5e6f7a8b9
Revises: a7c8e9f0b1a2
Create Date: 2025-10-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


def _has_table(table_name: str, bind) -> bool:
    """Return True if the database has the given table."""
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    """Return True if the given table already has the specified column."""
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "a7c8e9f0b1a2"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table = "purchase_invoice"

    if not _has_table(table, bind) or _has_column(table, "department", bind):
        return

    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.add_column(sa.Column("department", sa.String(length=50), nullable=True))


def downgrade():
    bind = op.get_bind()
    table = "purchase_invoice"

    if not _has_table(table, bind) or not _has_column(table, "department", bind):
        return

    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.drop_column("department")
