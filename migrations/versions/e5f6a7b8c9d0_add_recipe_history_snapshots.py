"""add recipe history snapshots

Revision ID: e5f6a7b8c9d0
Revises: e4f5a6b7c8d9
Create Date: 2026-05-13 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invoice_product_recipe_item_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_product_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("item_name", sa.String(length=100), nullable=False),
        sa.Column("base_unit", sa.String(length=50), nullable=True),
        sa.Column(
            "item_cost",
            sa.Float(),
            server_default="0.0",
            nullable=False,
        ),
        sa.Column("unit_name", sa.String(length=50), nullable=True),
        sa.Column(
            "unit_factor",
            sa.Float(),
            server_default="1.0",
            nullable=False,
        ),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column(
            "countable",
            sa.Boolean(),
            server_default="0",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["invoice_product_id"],
            ["invoice_product.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["unit_id"], ["item_unit.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_invoice_product_recipe_snapshot_invoice_product",
        "invoice_product_recipe_item_snapshot",
        ["invoice_product_id"],
        unique=False,
    )
    op.create_index(
        "ix_invoice_product_recipe_snapshot_item_id",
        "invoice_product_recipe_item_snapshot",
        ["item_id"],
        unique=False,
    )

    op.create_table(
        "terminal_sale_recipe_item_snapshot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("terminal_sale_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("unit_id", sa.Integer(), nullable=True),
        sa.Column("item_name", sa.String(length=100), nullable=False),
        sa.Column("base_unit", sa.String(length=50), nullable=True),
        sa.Column(
            "item_cost",
            sa.Float(),
            server_default="0.0",
            nullable=False,
        ),
        sa.Column("unit_name", sa.String(length=50), nullable=True),
        sa.Column(
            "unit_factor",
            sa.Float(),
            server_default="1.0",
            nullable=False,
        ),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column(
            "countable",
            sa.Boolean(),
            server_default="0",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["terminal_sale_id"],
            ["terminal_sale.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["item.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["unit_id"], ["item_unit.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_terminal_sale_recipe_snapshot_terminal_sale",
        "terminal_sale_recipe_item_snapshot",
        ["terminal_sale_id"],
        unique=False,
    )
    op.create_index(
        "ix_terminal_sale_recipe_snapshot_item_id",
        "terminal_sale_recipe_item_snapshot",
        ["item_id"],
        unique=False,
    )

    with op.batch_alter_table("event_stand_sheet_item") as batch_op:
        batch_op.add_column(
            sa.Column("item_name_snapshot", sa.String(length=100), nullable=True)
        )
        batch_op.add_column(
            sa.Column("item_base_unit_snapshot", sa.String(length=50), nullable=True)
        )
        batch_op.add_column(sa.Column("item_cost_snapshot", sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("price_per_unit_snapshot", sa.Float(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("event_stand_sheet_item") as batch_op:
        batch_op.drop_column("price_per_unit_snapshot")
        batch_op.drop_column("item_cost_snapshot")
        batch_op.drop_column("item_base_unit_snapshot")
        batch_op.drop_column("item_name_snapshot")

    op.drop_index(
        "ix_terminal_sale_recipe_snapshot_item_id",
        table_name="terminal_sale_recipe_item_snapshot",
    )
    op.drop_index(
        "ix_terminal_sale_recipe_snapshot_terminal_sale",
        table_name="terminal_sale_recipe_item_snapshot",
    )
    op.drop_table("terminal_sale_recipe_item_snapshot")

    op.drop_index(
        "ix_invoice_product_recipe_snapshot_item_id",
        table_name="invoice_product_recipe_item_snapshot",
    )
    op.drop_index(
        "ix_invoice_product_recipe_snapshot_invoice_product",
        table_name="invoice_product_recipe_item_snapshot",
    )
    op.drop_table("invoice_product_recipe_item_snapshot")
