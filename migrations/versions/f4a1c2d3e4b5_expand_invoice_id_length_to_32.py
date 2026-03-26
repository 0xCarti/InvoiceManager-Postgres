"""expand invoice id length to 32

Revision ID: f4a1c2d3e4b5
Revises: e3b7c9a1f4d2
Create Date: 2026-03-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f4a1c2d3e4b5"
down_revision = "e3b7c9a1f4d2"
branch_labels = None
depends_on = None


INVOICE_TABLE = "invoice"
INVOICE_PRODUCT_TABLE = "invoice_product"
INVOICE_ID_COLUMN = "id"
INVOICE_PRODUCT_INVOICE_ID_COLUMN = "invoice_id"
FK_NAME = "fk_invoice_product_invoice_id_invoice"


def _drop_existing_invoice_fk() -> None:
    """Drop any FK from invoice_product.invoice_id -> invoice.id when present."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for fk in inspector.get_foreign_keys(INVOICE_PRODUCT_TABLE):
        if (
            fk.get("constrained_columns") == [INVOICE_PRODUCT_INVOICE_ID_COLUMN]
            and fk.get("referred_table") == INVOICE_TABLE
            and fk.get("name")
        ):
            op.drop_constraint(fk["name"], INVOICE_PRODUCT_TABLE, type_="foreignkey")


def _alter_invoice_id_lengths(new_length: int, existing_length: int) -> None:
    with op.batch_alter_table(INVOICE_PRODUCT_TABLE, schema=None) as batch_op:
        batch_op.alter_column(
            INVOICE_PRODUCT_INVOICE_ID_COLUMN,
            existing_type=sa.String(length=existing_length),
            type_=sa.String(length=new_length),
            existing_nullable=False,
        )

    with op.batch_alter_table(INVOICE_TABLE, schema=None) as batch_op:
        batch_op.alter_column(
            INVOICE_ID_COLUMN,
            existing_type=sa.String(length=existing_length),
            type_=sa.String(length=new_length),
            existing_nullable=False,
        )


def _create_invoice_fk() -> None:
    with op.batch_alter_table(INVOICE_PRODUCT_TABLE, schema=None) as batch_op:
        batch_op.create_foreign_key(
            FK_NAME,
            INVOICE_TABLE,
            [INVOICE_PRODUCT_INVOICE_ID_COLUMN],
            [INVOICE_ID_COLUMN],
            ondelete="CASCADE",
        )


def upgrade() -> None:
    _drop_existing_invoice_fk()
    _alter_invoice_id_lengths(new_length=32, existing_length=10)
    _create_invoice_fk()


def downgrade() -> None:
    _drop_existing_invoice_fk()
    _alter_invoice_id_lengths(new_length=10, existing_length=32)
    _create_invoice_fk()
