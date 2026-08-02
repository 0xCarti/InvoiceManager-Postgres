"""add inventory expiry lots

Revision ID: 1a2b3c4d5e6f
Revises: 0a1b2c3d4e5f
Create Date: 2026-06-02 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "1a2b3c4d5e6f"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "item",
        sa.Column(
            "expiry_tracking_mode",
            sa.String(length=24),
            server_default="none",
            nullable=False,
        ),
    )
    op.add_column(
        "item",
        sa.Column("expiry_shelf_life_days", sa.Integer(), nullable=True),
    )
    op.add_column(
        "item",
        sa.Column(
            "expiry_warning_days",
            sa.Integer(),
            server_default="14",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_item_expiry_tracking_mode",
        "item",
        "expiry_tracking_mode IN ('none', 'received_date', 'shelf_life', 'exact')",
    )

    op.create_table(
        "inventory_expiry_lot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("purchase_invoice_id", sa.Integer(), nullable=True),
        sa.Column("purchase_invoice_item_id", sa.Integer(), nullable=True),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("original_quantity", sa.Float(), nullable=False),
        sa.Column("remaining_quantity", sa.Float(), nullable=False),
        sa.Column(
            "source_type",
            sa.String(length=32),
            server_default="purchase",
            nullable=False,
        ),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("source_line_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["location_id"], ["location.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["purchase_invoice_id"], ["purchase_invoice.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["purchase_invoice_item_id"],
            ["purchase_invoice_item.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_expiry_lot_item_location",
        "inventory_expiry_lot",
        ["item_id", "location_id"],
    )
    op.create_index(
        "ix_inventory_expiry_lot_expiry_date",
        "inventory_expiry_lot",
        ["expiry_date"],
    )
    op.create_index(
        "ix_inventory_expiry_lot_remaining",
        "inventory_expiry_lot",
        ["remaining_quantity"],
    )
    op.create_index(
        "ix_inventory_expiry_lot_source",
        "inventory_expiry_lot",
        ["source_type", "source_id", "source_line_id"],
    )

    op.create_table(
        "inventory_expiry_lot_adjustment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("lot_id", sa.Integer(), nullable=False),
        sa.Column("quantity_delta", sa.Float(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=64), nullable=True),
        sa.Column("source_line_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"], ["inventory_expiry_lot.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_inventory_expiry_lot_adjustment_source",
        "inventory_expiry_lot_adjustment",
        ["source_type", "source_id", "source_line_id"],
    )
    op.create_index(
        "ix_inventory_expiry_lot_adjustment_lot",
        "inventory_expiry_lot_adjustment",
        ["lot_id"],
    )


def downgrade():
    op.drop_index(
        "ix_inventory_expiry_lot_adjustment_lot",
        table_name="inventory_expiry_lot_adjustment",
    )
    op.drop_index(
        "ix_inventory_expiry_lot_adjustment_source",
        table_name="inventory_expiry_lot_adjustment",
    )
    op.drop_table("inventory_expiry_lot_adjustment")
    op.drop_index("ix_inventory_expiry_lot_source", table_name="inventory_expiry_lot")
    op.drop_index("ix_inventory_expiry_lot_remaining", table_name="inventory_expiry_lot")
    op.drop_index(
        "ix_inventory_expiry_lot_expiry_date", table_name="inventory_expiry_lot"
    )
    op.drop_index(
        "ix_inventory_expiry_lot_item_location", table_name="inventory_expiry_lot"
    )
    op.drop_table("inventory_expiry_lot")
    op.drop_constraint("ck_item_expiry_tracking_mode", "item", type_="check")
    op.drop_column("item", "expiry_warning_days")
    op.drop_column("item", "expiry_shelf_life_days")
    op.drop_column("item", "expiry_tracking_mode")
