"""Ensure unit cost column exists on purchase order items"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202502050001"
down_revision = "202501150001"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    if _has_table("purchase_order_item", bind) and not _has_column(
        "purchase_order_item", "unit_cost", bind
    ):
        op.add_column(
            "purchase_order_item", sa.Column("unit_cost", sa.Float(), nullable=True)
        )

    if _has_table("purchase_order_item_archive", bind) and not _has_column(
        "purchase_order_item_archive", "unit_cost", bind
    ):
        op.add_column(
            "purchase_order_item_archive",
            sa.Column("unit_cost", sa.Float(), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    if _has_table("purchase_order_item_archive", bind) and _has_column(
        "purchase_order_item_archive", "unit_cost", bind
    ):
        op.drop_column("purchase_order_item_archive", "unit_cost")

    if _has_table("purchase_order_item", bind) and _has_column(
        "purchase_order_item", "unit_cost", bind
    ):
        op.drop_column("purchase_order_item", "unit_cost")
