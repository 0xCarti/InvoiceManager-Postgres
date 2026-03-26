"""fix invoice_product.invoice_id foreign key

Revision ID: d2f7a1b9c8e0
Revises: c1a2b3d4e5f6
Create Date: 2026-03-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "d2f7a1b9c8e0"
down_revision = "c1a2b3d4e5f6"
branch_labels = None
depends_on = None


TABLE_NAME = "invoice_product"
COLUMN_NAME = "invoice_id"
REFERENT_TABLE = "invoice"
FK_NAME = "fk_invoice_product_invoice_id_invoice"


def _drop_existing_invoice_fk() -> None:
    """Drop any FK from invoice_product.invoice_id -> invoice.id when present."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for fk in inspector.get_foreign_keys(TABLE_NAME):
        if (
            fk.get("constrained_columns") == [COLUMN_NAME]
            and fk.get("referred_table") == REFERENT_TABLE
            and fk.get("name")
        ):
            op.drop_constraint(fk["name"], TABLE_NAME, type_="foreignkey")


def upgrade() -> None:
    _drop_existing_invoice_fk()

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        batch_op.create_foreign_key(
            FK_NAME,
            REFERENT_TABLE,
            [COLUMN_NAME],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    _drop_existing_invoice_fk()

    with op.batch_alter_table(TABLE_NAME, schema=None) as batch_op:
        batch_op.create_foreign_key(
            FK_NAME,
            REFERENT_TABLE,
            [COLUMN_NAME],
            ["id"],
        )
