"""add purchase invoice item gl code

Revision ID: e1b5c3f4d6a7
Revises: 4bde7cda3c1c
Create Date: 2025-02-08 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "e1b5c3f4d6a7"
down_revision = "4bde7cda3c1c"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {col["name"] for col in inspector.get_columns(table_name)}


def _has_fk(table_name: str, fk_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(fk["name"] == fk_name for fk in inspector.get_foreign_keys(table_name))


def upgrade():
    bind = op.get_bind()
    table = "purchase_invoice_item"
    fk_name = "fk_purchase_invoice_item_purchase_gl_code"

    if not _has_table(table, bind):
        return

    has_column = _has_column(table, "purchase_gl_code_id", bind)
    has_fk = _has_fk(table, fk_name, bind)

    with op.batch_alter_table(table, recreate="always") as batch_op:
        if not has_column:
            batch_op.add_column(
                sa.Column("purchase_gl_code_id", sa.Integer(), nullable=True)
            )
        if not has_fk:
            batch_op.create_foreign_key(
                fk_name,
                "gl_code",
                ["purchase_gl_code_id"],
                ["id"],
            )


def downgrade():
    bind = op.get_bind()
    table = "purchase_invoice_item"
    fk_name = "fk_purchase_invoice_item_purchase_gl_code"

    if not _has_table(table, bind):
        return

    has_fk = _has_fk(table, fk_name, bind)
    has_column = _has_column(table, "purchase_gl_code_id", bind)

    with op.batch_alter_table(table, recreate="always") as batch_op:
        if has_fk:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        if has_column:
            batch_op.drop_column("purchase_gl_code_id")
