from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202503050001"
down_revision = "202502050001"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    columns = [col["name"] for col in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "purchase_order"
    if not _has_table(table_name, bind):
        return

    if not _has_column(table_name, "order_number", bind):
        op.add_column(
            table_name,
            sa.Column("order_number", sa.String(length=100), nullable=True),
        )

    if not _has_column(table_name, "expected_total_cost", bind):
        op.add_column(
            table_name,
            sa.Column("expected_total_cost", sa.Float(), nullable=True),
        )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "purchase_order"
    if not _has_table(table_name, bind):
        return

    if _has_column(table_name, "expected_total_cost", bind):
        op.drop_column(table_name, "expected_total_cost")

    if _has_column(table_name, "order_number", bind):
        op.drop_column(table_name, "order_number")
