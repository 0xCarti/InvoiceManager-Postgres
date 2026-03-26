"""Add recipe yield fields to product"""

import sqlalchemy as sa
from alembic import op


def _has_column(table_name: str, column_name: str, bind) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return column_name in {
        column["name"] for column in inspector.get_columns(table_name)
    }


# revision identifiers, used by Alembic.
revision = "202408010001"
down_revision = "202407171234"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    with op.batch_alter_table("product", recreate="always") as batch_op:
        if not _has_column("product", "recipe_yield_quantity", bind):
            batch_op.add_column(
                sa.Column(
                    "recipe_yield_quantity",
                    sa.Float(),
                    nullable=False,
                    server_default="1.0",
                )
            )
        if not _has_column("product", "recipe_yield_unit", bind):
            batch_op.add_column(
                sa.Column("recipe_yield_unit", sa.String(length=50), nullable=True)
            )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    with op.batch_alter_table("product", recreate="always") as batch_op:
        if _has_column("product", "recipe_yield_unit", bind):
            batch_op.drop_column("recipe_yield_unit")
        if _has_column("product", "recipe_yield_quantity", bind):
            batch_op.drop_column("recipe_yield_quantity")
