"""Make terminal-sale recipe snapshots complete and yield-stable.

Revision ID: 8c9d0e1f2a3b
Revises: 7b8c9d0e1f2a
Create Date: 2026-08-15
"""

import sqlalchemy as sa
from alembic import op

revision = "8c9d0e1f2a3b"
down_revision = "7b8c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "terminal_sale",
        sa.Column(
            "recipe_snapshot_captured",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "terminal_sale_recipe_item_snapshot",
        sa.Column("recipe_yield_quantity", sa.Float(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE terminal_sale_recipe_item_snapshot AS snapshot "
            "SET recipe_yield_quantity = COALESCE(NULLIF(product.recipe_yield_quantity, 0), 1) "
            "FROM terminal_sale AS sale "
            "JOIN product ON product.id = sale.product_id "
            "WHERE snapshot.terminal_sale_id = sale.id"
        )
    )
    op.execute(
        sa.text(
            "UPDATE terminal_sale_recipe_item_snapshot AS snapshot "
            "SET countable = TRUE "
            "FROM terminal_sale AS sale "
            "WHERE snapshot.terminal_sale_id = sale.id "
            "AND EXISTS ("
            "SELECT 1 FROM event_stand_sheet_item AS sheet "
            "WHERE sheet.event_location_id = sale.event_location_id "
            "AND sheet.item_id = snapshot.item_id"
            ") "
            "AND NOT EXISTS ("
            "SELECT 1 FROM event_location AS event_location "
            "JOIN location_stand_item AS location_item "
            "ON location_item.location_id = event_location.location_id "
            "WHERE event_location.id = sale.event_location_id "
            "AND location_item.item_id = snapshot.item_id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE terminal_sale_recipe_item_snapshot AS snapshot "
            "SET countable = (location_item.active AND location_item.countable) "
            "FROM terminal_sale AS sale "
            "JOIN event_location AS event_location "
            "ON event_location.id = sale.event_location_id "
            "JOIN location_stand_item AS location_item "
            "ON location_item.location_id = event_location.location_id "
            "WHERE snapshot.terminal_sale_id = sale.id "
            "AND location_item.item_id = snapshot.item_id"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO terminal_sale_recipe_item_snapshot "
            "(terminal_sale_id, item_id, unit_id, item_name, base_unit, "
            "item_cost, unit_name, unit_factor, quantity, "
            "recipe_yield_quantity, countable) "
            "SELECT sale.id, recipe.item_id, recipe.unit_id, item.name, "
            "item.base_unit, COALESCE(item.cost, 0), unit.name, "
            "COALESCE(NULLIF(unit.factor, 0), 1), recipe.quantity, "
            "COALESCE(NULLIF(product.recipe_yield_quantity, 0), 1), "
            "CASE WHEN location_item.id IS NOT NULL "
            "THEN (location_item.active AND location_item.countable) "
            "WHEN EXISTS ("
            "SELECT 1 FROM event_stand_sheet_item AS sheet "
            "WHERE sheet.event_location_id = sale.event_location_id "
            "AND sheet.item_id = recipe.item_id"
            ") THEN TRUE "
            "ELSE recipe.countable END "
            "FROM terminal_sale AS sale "
            "JOIN product ON product.id = sale.product_id "
            "JOIN event_location AS event_location "
            "ON event_location.id = sale.event_location_id "
            "JOIN product_recipe_item AS recipe "
            "ON recipe.product_id = product.id "
            "JOIN item ON item.id = recipe.item_id "
            "LEFT JOIN item_unit AS unit ON unit.id = recipe.unit_id "
            "LEFT JOIN location_stand_item AS location_item "
            "ON location_item.location_id = event_location.location_id "
            "AND location_item.item_id = recipe.item_id "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM terminal_sale_recipe_item_snapshot AS existing "
            "WHERE existing.terminal_sale_id = sale.id"
            ")"
        )
    )
    op.execute(
        sa.text(
            "UPDATE terminal_sale SET recipe_snapshot_captured = TRUE"
        )
    )
    op.alter_column(
        "terminal_sale_recipe_item_snapshot",
        "recipe_yield_quantity",
        existing_type=sa.Float(),
        nullable=False,
        server_default="1.0",
    )


def downgrade():
    op.drop_column("terminal_sale", "recipe_snapshot_captured")
    op.drop_column(
        "terminal_sale_recipe_item_snapshot",
        "recipe_yield_quantity",
    )
