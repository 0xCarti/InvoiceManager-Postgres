"""Add variance details to terminal sales summary"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202411200001"
down_revision = "202411010001"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(col["name"] == column_name for col in columns)


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "event_location_terminal_sales_summary"
    column_name = "variance_details"

    if not _has_table(table_name, bind):
        return

    if _has_column(table_name, column_name, bind):
        return

    op.add_column(table_name, sa.Column(column_name, sa.JSON(), nullable=True))


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "event_location_terminal_sales_summary"
    column_name = "variance_details"

    if not _has_table(table_name, bind):
        return

    if not _has_column(table_name, column_name, bind):
        return

    op.drop_column(table_name, column_name)
