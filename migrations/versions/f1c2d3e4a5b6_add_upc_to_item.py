"""add upc column to item

Revision ID: f1c2d3e4a5b6
Revises: e1b5c3f4d6a7
Create Date: 2025-09-10 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _has_unique_constraint(table_name: str, constraint_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return constraint_name in {
        constraint["name"] for constraint in inspector.get_unique_constraints(table_name)
    }


# revision identifiers, used by Alembic.
revision = "f1c2d3e4a5b6"
down_revision = "e1b5c3f4d6a7"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    with op.batch_alter_table("item", recreate="always") as batch_op:
        if not _has_column("item", "upc", bind):
            batch_op.add_column(sa.Column("upc", sa.String(length=32), nullable=True))
        if not _has_unique_constraint("item", "uq_item_upc", bind):
            batch_op.create_unique_constraint("uq_item_upc", ["upc"])


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    with op.batch_alter_table("item", recreate="always") as batch_op:
        if _has_unique_constraint("item", "uq_item_upc", bind):
            batch_op.drop_constraint("uq_item_upc", type_="unique")
        if _has_column("item", "upc", bind):
            batch_op.drop_column("upc")
