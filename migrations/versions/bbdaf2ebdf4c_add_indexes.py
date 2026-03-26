"""add indexes

Revision ID: bbdaf2ebdf4c
Revises: 202408010001
Create Date: 2025-09-06 05:37:39.070861

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "bbdaf2ebdf4c"
down_revision = "202408010001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_location_archived", "location", ["archived"], if_not_exists=True
    )
    op.create_index(
        "ix_item_archived", "item", ["archived"], if_not_exists=True
    )
    op.create_index(
        "ix_transfer_to_location_completed",
        "transfer",
        ["to_location_id", "completed"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_transfer_date_created",
        "transfer",
        ["date_created"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_transfer_user_id", "transfer", ["user_id"], if_not_exists=True
    )
    op.create_index(
        "ix_customer_archived", "customer", ["archived"], if_not_exists=True
    )
    op.create_index(
        "ix_vendor_archived", "vendor", ["archived"], if_not_exists=True
    )
    op.create_index(
        "ix_invoice_date_created",
        "invoice",
        ["date_created"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_invoice_customer_id",
        "invoice",
        ["customer_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_invoice_user_id", "invoice", ["user_id"], if_not_exists=True
    )


def downgrade():
    op.drop_index("ix_invoice_user_id", table_name="invoice", if_exists=True)
    op.drop_index(
        "ix_invoice_customer_id", table_name="invoice", if_exists=True
    )
    op.drop_index(
        "ix_invoice_date_created", table_name="invoice", if_exists=True
    )
    op.drop_index("ix_vendor_archived", table_name="vendor", if_exists=True)
    op.drop_index(
        "ix_customer_archived", table_name="customer", if_exists=True
    )
    op.drop_index("ix_transfer_user_id", table_name="transfer", if_exists=True)
    op.drop_index(
        "ix_transfer_date_created", table_name="transfer", if_exists=True
    )
    op.drop_index(
        "ix_transfer_to_location_completed",
        table_name="transfer",
        if_exists=True,
    )
    op.drop_index("ix_item_archived", table_name="item", if_exists=True)
    op.drop_index(
        "ix_location_archived", table_name="location", if_exists=True
    )
