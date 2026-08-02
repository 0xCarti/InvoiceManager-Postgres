"""Add product sellable amounts.

Revision ID: 4d5e6f7a8b9c
Revises: 3c4d5e6f7a8b
Create Date: 2026-07-07
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "4d5e6f7a8b9c"
down_revision = "3c4d5e6f7a8b"
branch_labels = None
depends_on = None


def _float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def upgrade():
    op.create_table(
        "product_sellable_amount",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), server_default="Each", nullable=False),
        sa.Column("quantity", sa.Float(), server_default="1.0", nullable=False),
        sa.Column("price", sa.Numeric(precision=10, scale=2), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("price >= 0", name="ck_product_sellable_amount_price"),
        sa.CheckConstraint("quantity > 0", name="ck_product_sellable_amount_quantity"),
        sa.ForeignKeyConstraint(["product_id"], ["product.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_product_sellable_amount_product_id",
        "product_sellable_amount",
        ["product_id"],
    )
    op.create_index(
        "ix_product_sellable_amount_product_active",
        "product_sellable_amount",
        ["product_id", "active"],
    )

    op.create_table(
        "menu_sellable_amounts",
        sa.Column("menu_id", sa.Integer(), nullable=False),
        sa.Column("sellable_amount_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["menu_id"], ["menu.id"]),
        sa.ForeignKeyConstraint(["sellable_amount_id"], ["product_sellable_amount.id"]),
        sa.PrimaryKeyConstraint("menu_id", "sellable_amount_id"),
    )

    op.add_column(
        "invoice_product",
        sa.Column("sellable_amount_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "invoice_product",
        sa.Column("sellable_amount_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "invoice_product",
        sa.Column("sellable_quantity", sa.Float(), server_default="1.0", nullable=False),
    )
    op.create_foreign_key(
        "fk_invoice_product_sellable_amount_id",
        "invoice_product",
        "product_sellable_amount",
        ["sellable_amount_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "terminal_sale",
        sa.Column("sellable_amount_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "terminal_sale",
        sa.Column("sellable_amount_name", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "terminal_sale",
        sa.Column("sellable_quantity", sa.Float(), server_default="1.0", nullable=False),
    )
    op.add_column(
        "terminal_sale",
        sa.Column("unit_price_snapshot", sa.Float(), nullable=True),
    )
    op.add_column(
        "terminal_sale",
        sa.Column("line_total_snapshot", sa.Float(), nullable=True),
    )
    op.create_foreign_key(
        "fk_terminal_sale_sellable_amount_id",
        "terminal_sale",
        "product_sellable_amount",
        ["sellable_amount_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "pos_sales_import_row",
        sa.Column("sellable_amount_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_pos_sales_import_row_sellable_amount_id",
        "pos_sales_import_row",
        "product_sellable_amount",
        ["sellable_amount_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_pos_sales_import_row_sellable_amount_id",
        "pos_sales_import_row",
        ["sellable_amount_id"],
    )

    bind = op.get_bind()
    product_amount_ids: dict[int, list[tuple[int, float]]] = {}
    product_rows = bind.execute(
        sa.text("SELECT id, price, invoice_sale_price FROM product ORDER BY id")
    ).mappings()
    for row in product_rows:
        product_id = int(row["id"])
        terminal_price = _float(row.get("price"))
        amount_ids: list[tuple[int, float]] = []
        result = bind.execute(
            sa.text(
                """
                INSERT INTO product_sellable_amount
                    (product_id, name, quantity, price, active, is_default, position)
                VALUES
                    (:product_id, :name, 1.0, :price, true, true, 0)
                RETURNING id
                """
            ),
            {
                "product_id": product_id,
                "name": "Each",
                "price": terminal_price,
            },
        )
        default_id = int(result.scalar_one())
        amount_ids.append((default_id, terminal_price))

        invoice_price_raw = row.get("invoice_sale_price")
        if invoice_price_raw is not None:
            invoice_price = _float(invoice_price_raw)
            if abs(invoice_price - terminal_price) > 0.01:
                result = bind.execute(
                    sa.text(
                        """
                        INSERT INTO product_sellable_amount
                            (product_id, name, quantity, price, active, is_default, position)
                        VALUES
                            (:product_id, :name, 1.0, :price, true, false, 1)
                        RETURNING id
                        """
                    ),
                    {
                        "product_id": product_id,
                        "name": "Customer Each",
                        "price": invoice_price,
                    },
                )
                amount_ids.append((int(result.scalar_one()), invoice_price))

        product_amount_ids[product_id] = amount_ids

    for product_id, amount_ids in product_amount_ids.items():
        default_id = amount_ids[0][0]
        bind.execute(
            sa.text(
                """
                INSERT INTO menu_sellable_amounts (menu_id, sellable_amount_id)
                SELECT menu_id, :amount_id
                FROM menu_products
                WHERE product_id = :product_id
                """
            ),
            {"product_id": product_id, "amount_id": default_id},
        )

    invoice_rows = bind.execute(
        sa.text(
            """
            SELECT id, product_id, unit_price
            FROM invoice_product
            WHERE product_id IS NOT NULL
            ORDER BY id
            """
        )
    ).mappings()
    for row in invoice_rows:
        product_id = int(row["product_id"])
        amount_ids = product_amount_ids.get(product_id) or []
        if not amount_ids:
            continue
        unit_price = _float(row.get("unit_price"))
        amount_id = amount_ids[0][0]
        amount_name = "Each"
        for candidate_id, candidate_price in amount_ids:
            if abs(candidate_price - unit_price) <= 0.01:
                amount_id = candidate_id
                amount_name = "Customer Each" if candidate_id != amount_ids[0][0] else "Each"
                break
        bind.execute(
            sa.text(
                """
                UPDATE invoice_product
                SET sellable_amount_id = :amount_id,
                    sellable_amount_name = :amount_name,
                    sellable_quantity = 1.0
                WHERE id = :line_id
                """
            ),
            {
                "line_id": row["id"],
                "amount_id": amount_id,
                "amount_name": amount_name,
            },
        )

    terminal_rows = bind.execute(
        sa.text(
            """
            SELECT ts.id, ts.product_id, ts.quantity, p.price
            FROM terminal_sale ts
            JOIN product p ON p.id = ts.product_id
            ORDER BY ts.id
            """
        )
    ).mappings()
    for row in terminal_rows:
        product_id = int(row["product_id"])
        amount_ids = product_amount_ids.get(product_id) or []
        if not amount_ids:
            continue
        amount_id, price = amount_ids[0]
        quantity = _float(row.get("quantity"))
        bind.execute(
            sa.text(
                """
                UPDATE terminal_sale
                SET sellable_amount_id = :amount_id,
                    sellable_amount_name = 'Each',
                    sellable_quantity = 1.0,
                    unit_price_snapshot = :price,
                    line_total_snapshot = :line_total
                WHERE id = :sale_id
                """
            ),
            {
                "sale_id": row["id"],
                "amount_id": amount_id,
                "price": price,
                "line_total": quantity * price,
            },
        )

    for product_id, amount_ids in product_amount_ids.items():
        bind.execute(
            sa.text(
                """
                UPDATE pos_sales_import_row
                SET sellable_amount_id = :amount_id
                WHERE product_id = :product_id
                  AND sellable_amount_id IS NULL
                """
            ),
            {"product_id": product_id, "amount_id": amount_ids[0][0]},
        )


def downgrade():
    op.drop_index("ix_pos_sales_import_row_sellable_amount_id", table_name="pos_sales_import_row")
    op.drop_constraint(
        "fk_pos_sales_import_row_sellable_amount_id",
        "pos_sales_import_row",
        type_="foreignkey",
    )
    op.drop_column("pos_sales_import_row", "sellable_amount_id")

    op.drop_constraint(
        "fk_terminal_sale_sellable_amount_id",
        "terminal_sale",
        type_="foreignkey",
    )
    op.drop_column("terminal_sale", "line_total_snapshot")
    op.drop_column("terminal_sale", "unit_price_snapshot")
    op.drop_column("terminal_sale", "sellable_quantity")
    op.drop_column("terminal_sale", "sellable_amount_name")
    op.drop_column("terminal_sale", "sellable_amount_id")

    op.drop_constraint(
        "fk_invoice_product_sellable_amount_id",
        "invoice_product",
        type_="foreignkey",
    )
    op.drop_column("invoice_product", "sellable_quantity")
    op.drop_column("invoice_product", "sellable_amount_name")
    op.drop_column("invoice_product", "sellable_amount_id")

    op.drop_table("menu_sellable_amounts")
    op.drop_index(
        "ix_product_sellable_amount_product_active",
        table_name="product_sellable_amount",
    )
    op.drop_index(
        "ix_product_sellable_amount_product_id",
        table_name="product_sellable_amount",
    )
    op.drop_table("product_sellable_amount")
