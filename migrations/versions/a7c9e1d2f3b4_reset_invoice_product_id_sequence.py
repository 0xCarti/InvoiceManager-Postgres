"""reset invoice_product.id sequence to max id

Revision ID: a7c9e1d2f3b4
Revises: f4a1c2d3e4b5
Create Date: 2026-03-26 00:00:00.000000

This migration addresses PostgreSQL duplicate primary key failures such as:
"Key (id)=(N) already exists" on invoice_product inserts after restores.
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "a7c9e1d2f3b4"
down_revision = "f4a1c2d3e4b5"
branch_labels = None
depends_on = None


TABLE_NAME = "invoice_product"
COLUMN_NAME = "id"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    sequence_name = bind.execute(
        sa.text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
        {"table_name": TABLE_NAME, "column_name": COLUMN_NAME},
    ).scalar_one_or_none()

    # No-op guard: some schemas may not have a serial/identity-backed sequence.
    if not sequence_name:
        return

    bind.execute(
        sa.text(
            """
            SELECT setval(
                CAST(:sequence_name AS regclass),
                COALESCE((SELECT MAX(id) FROM invoice_product), 1),
                true
            )
            """
        ),
        {"sequence_name": sequence_name},
    )


def downgrade() -> None:
    # Irreversible/no-op: sequence reconciliation only adjusts runtime sequence state.
    pass
