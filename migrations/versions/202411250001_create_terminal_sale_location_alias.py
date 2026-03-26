"""Create terminal sale location alias"""

from alembic import op
import sqlalchemy as sa


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


# revision identifiers, used by Alembic.
revision = "202411250001"
down_revision = "202411200001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "terminal_sale_location_alias"
    if _has_table(table_name, bind):
        return

    op.create_table(
        table_name,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"]),
        sa.UniqueConstraint("normalized_name"),
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    table_name = "terminal_sale_location_alias"
    if not _has_table(table_name, bind):
        return

    op.drop_table(table_name)
