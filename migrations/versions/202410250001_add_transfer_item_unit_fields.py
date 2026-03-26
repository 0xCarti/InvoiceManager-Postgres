"""Add unit tracking columns to transfer items."""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "202410250001"
down_revision = "202410050001"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def _has_foreign_key(table_name: str, constraint_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(fk["name"] == constraint_name for fk in inspector.get_foreign_keys(table_name))


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "transfer_item"
    if not _has_table(table_name, bind):
        return

    columns_to_add = []
    if not _has_column(table_name, "unit_id", bind):
        columns_to_add.append(
            sa.Column("unit_id", sa.Integer(), nullable=True)
        )
    if not _has_column(table_name, "unit_quantity", bind):
        columns_to_add.append(
            sa.Column("unit_quantity", sa.Float(), nullable=True)
        )
    if not _has_column(table_name, "base_quantity", bind):
        columns_to_add.append(
            sa.Column("base_quantity", sa.Float(), nullable=True)
        )

    fk_name = "fk_transfer_item_unit_id_item_unit"
    needs_fk = _has_table("item_unit", bind) and not _has_foreign_key(
        table_name, fk_name, bind
    )

    if not columns_to_add and not needs_fk:
        return

    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        for column in columns_to_add:
            batch_op.add_column(column)
        if needs_fk:
            batch_op.create_foreign_key(
                fk_name,
                "item_unit",
                ["unit_id"],
                ["id"],
            )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "transfer_item"
    if not _has_table(table_name, bind):
        return

    fk_name = "fk_transfer_item_unit_id_item_unit"
    drop_fk = _has_foreign_key(table_name, fk_name, bind)

    columns_to_drop = [
        col
        for col in ["base_quantity", "unit_quantity", "unit_id"]
        if _has_column(table_name, col, bind)
    ]

    if not columns_to_drop and not drop_fk:
        return

    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        if drop_fk:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        for column_name in columns_to_drop:
            batch_op.drop_column(column_name)
