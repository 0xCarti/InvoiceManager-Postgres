"""create event location terminal sales summary"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "202411010001"
down_revision = "202410250001"
branch_labels = None
depends_on = None


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "event_location_terminal_sales_summary"
    if _has_table(table_name, bind):
        return

    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event_location_id", sa.Integer(), nullable=False),
        sa.Column("source_location", sa.String(length=255), nullable=True),
        sa.Column("total_quantity", sa.Float(), nullable=True),
        sa.Column("total_amount", sa.Float(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["event_location_id"],
            ["event_location.id"],
            name="fk_event_location_terminal_sales_summary_el",
        ),
        sa.UniqueConstraint(
            "event_location_id",
            name="uq_event_location_terminal_sales_summary_el",
        ),
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "event_location_terminal_sales_summary"
    if not _has_table(table_name, bind):
        return

    op.drop_table(table_name)
