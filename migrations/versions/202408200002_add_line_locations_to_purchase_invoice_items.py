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
revision = "202408200002"
down_revision = "1f2e3d4c5b6a"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not bind:
        return

    with op.batch_alter_table("purchase_invoice_item", recreate="always") as batch_op:
        if not _has_column("purchase_invoice_item", "location_id", bind):
            batch_op.add_column(
                sa.Column("location_id", sa.Integer(), nullable=True)
            )
            batch_op.create_foreign_key(
                "fk_purchase_invoice_item_location_id",
                "location",
                ["location_id"],
                ["id"],
                ondelete="SET NULL",
            )

    op.execute(
        sa.text(
            """
            UPDATE purchase_invoice_item AS pii
            SET location_id = (
                SELECT location_id
                FROM purchase_invoice AS pi
                WHERE pi.id = pii.invoice_id
            )
            WHERE location_id IS NULL
            """
        )
    )


def downgrade():
    bind = op.get_bind()
    if not bind:
        return

    with op.batch_alter_table("purchase_invoice_item", recreate="always") as batch_op:
        if _has_column("purchase_invoice_item", "location_id", bind):
            batch_op.drop_constraint(
                "fk_purchase_invoice_item_location_id", type_="foreignkey"
            )
            batch_op.drop_column("location_id")
