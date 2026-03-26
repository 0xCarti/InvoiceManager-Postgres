"""add items_per_page to user

Revision ID: d2e1f5f3e1e4
Revises: c2f321f4c8b5
Create Date: 2025-01-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "d2e1f5f3e1e4"
down_revision = "c2f321f4c8b5"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    bind = op.get_bind()
    if not _has_table("user", bind):
        return
    if _has_column("user", "items_per_page", bind):
        return

    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "items_per_page",
                sa.Integer(),
                nullable=False,
                server_default="20",
            )
        )

    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.alter_column("items_per_page", server_default=None)


def downgrade():
    bind = op.get_bind()
    if not _has_table("user", bind):
        return
    if not _has_column("user", "items_per_page", bind):
        return

    with op.batch_alter_table("user", recreate="always") as batch_op:
        batch_op.drop_column("items_per_page")
