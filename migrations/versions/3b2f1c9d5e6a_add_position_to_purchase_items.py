"""add position columns to purchase items

Revision ID: 3b2f1c9d5e6a
Revises: d2e1f5f3e1e4
Create Date: 2025-01-01 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "3b2f1c9d5e6a"
down_revision = "d2e1f5f3e1e4"
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


def _add_position_column(table: str, group_column: str, bind) -> None:
    if not _has_table(table, bind) or _has_column(table, "position", bind):
        return

    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "position",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )

    op.execute(
        sa.text(
            f"""
            WITH ordered AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY {group_column}
                        ORDER BY id
                    ) - 1 AS rn
                FROM {table}
            )
            UPDATE {table}
            SET position = (
                SELECT ordered.rn FROM ordered WHERE ordered.id = {table}.id
            )
            """
        )
    )

    with op.batch_alter_table(table, recreate="always") as batch_op:
        batch_op.alter_column("position", server_default=None)


def upgrade():
    bind = op.get_bind()
    _add_position_column("purchase_order_item", "purchase_order_id", bind)
    _add_position_column("purchase_invoice_item", "invoice_id", bind)
    _add_position_column("purchase_order_item_archive", "purchase_order_id", bind)


def downgrade():
    bind = op.get_bind()
    for table in (
        "purchase_invoice_item",
        "purchase_order_item",
        "purchase_order_item_archive",
    ):
        if not _has_table(table, bind) or not _has_column(table, "position", bind):
            continue
        with op.batch_alter_table(table, recreate="always") as batch_op:
            batch_op.drop_column("position")
