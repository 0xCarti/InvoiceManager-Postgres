"""Add completion tracking fields to transfer items.

Revision ID: 202503130001
Revises: f1c2d3e4a5b6
Create Date: 2025-03-13 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "202503130001"
down_revision = "f1c2d3e4a5b6"
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
    if not _has_column(table_name, "completed_quantity", bind):
        columns_to_add.append(
            sa.Column(
                "completed_quantity",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
    if not _has_column(table_name, "completed_at", bind):
        columns_to_add.append(
            sa.Column("completed_at", sa.DateTime(), nullable=True)
        )
    if not _has_column(table_name, "completed_by_id", bind):
        columns_to_add.append(
            sa.Column("completed_by_id", sa.Integer(), nullable=True)
        )

    fk_name = "fk_transfer_item_completed_by_id_user"
    needs_fk = _has_table("user", bind) and not _has_foreign_key(
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
                "user",
                ["completed_by_id"],
                ["id"],
            )

    if not _has_table("transfer", bind):
        return

    transfer_item = sa.table(
        "transfer_item",
        sa.column("transfer_id"),
        sa.column("quantity"),
        sa.column("completed_quantity"),
        sa.column("completed_at"),
        sa.column("completed_by_id"),
    )
    transfer = sa.table(
        "transfer",
        sa.column("id"),
        sa.column("completed"),
        sa.column("date_created"),
        sa.column("user_id"),
    )

    completed_transfers = sa.select(transfer.c.id).where(
        transfer.c.completed == sa.true()
    )
    completed_at_subquery = (
        sa.select(transfer.c.date_created)
        .where(transfer.c.id == transfer_item.c.transfer_id)
        .scalar_subquery()
    )
    completed_by_subquery = (
        sa.select(transfer.c.user_id)
        .where(transfer.c.id == transfer_item.c.transfer_id)
        .scalar_subquery()
    )
    op.execute(
        transfer_item.update()
        .where(transfer_item.c.transfer_id.in_(completed_transfers))
        .values(
            completed_quantity=transfer_item.c.quantity,
            completed_at=completed_at_subquery,
            completed_by_id=completed_by_subquery,
        )
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "transfer_item"
    if not _has_table(table_name, bind):
        return

    fk_name = "fk_transfer_item_completed_by_id_user"
    drop_fk = _has_foreign_key(table_name, fk_name, bind)

    columns_to_drop = [
        col
        for col in ["completed_by_id", "completed_at", "completed_quantity"]
        if _has_column(table_name, col, bind)
    ]

    if not columns_to_drop and not drop_fk:
        return

    with op.batch_alter_table(table_name, recreate="always") as batch_op:
        if drop_fk:
            batch_op.drop_constraint(fk_name, type_="foreignkey")
        for column_name in columns_to_drop:
            batch_op.drop_column(column_name)
