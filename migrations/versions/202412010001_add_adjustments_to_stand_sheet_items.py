from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202411250002"
down_revision = "202411250001"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "event_stand_sheet_item"
    column_name = "adjustments"

    if not _has_table(table_name, bind):
        return

    if _has_column(table_name, column_name, bind):
        return

    op.add_column(
        table_name,
        sa.Column(column_name, sa.Float(), nullable=False, server_default="0.0"),
    )

    if bind.dialect.name != "sqlite":
        op.alter_column(table_name, column_name, server_default=None)


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "event_stand_sheet_item"
    column_name = "adjustments"

    if not _has_table(table_name, bind):
        return

    if not _has_column(table_name, column_name, bind):
        return

    op.drop_column(table_name, column_name)
