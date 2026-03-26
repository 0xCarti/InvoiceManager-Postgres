"""create terminal sale product alias"""

from alembic import op
import sqlalchemy as sa


def _has_table(table_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


# revision identifiers, used by Alembic.
revision = "202410050001"
down_revision = "202409200001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    if _has_table("terminal_sale_product_alias", bind):
        return

    op.create_table(
        "terminal_sale_product_alias",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("normalized_name", sa.String(length=255), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"]),
        sa.UniqueConstraint("normalized_name"),
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    if not _has_table("terminal_sale_product_alias", bind):
        return

    op.drop_table("terminal_sale_product_alias")
