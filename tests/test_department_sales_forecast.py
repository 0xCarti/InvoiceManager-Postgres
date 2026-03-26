import pytest

from app import db
from app.models import Item, ItemUnit, Product, ProductRecipeItem
from app.routes.report_routes import _calculate_department_usage
from app.utils.units import get_unit_label


@pytest.mark.usefixtures("gl_codes")
def test_calculate_department_usage_includes_unit_details(app):
    with app.app_context():
        item_with_receiving = Item(
            name="Case Limes",
            base_unit="each",
            cost=0.5,
            gl_code="5000",
        )
        item_without_receiving = Item(
            name="Salt Rim",
            base_unit="gram",
            cost=0.2,
            gl_code="5000",
        )
        db.session.add_all([item_with_receiving, item_without_receiving])
        db.session.flush()

        db.session.add(
            ItemUnit(
                item=item_with_receiving,
                name="Case",
                factor=12,
                receiving_default=True,
            )
        )

        product = Product(name="Margarita", price=10.0, cost=4.0)
        db.session.add(product)
        db.session.flush()

        db.session.add_all(
            [
                ProductRecipeItem(
                    product=product,
                    item=item_with_receiving,
                    quantity=2.0,
                ),
                ProductRecipeItem(
                    product=product,
                    item=item_without_receiving,
                    quantity=0.5,
                ),
            ]
        )
        db.session.commit()

        payload = {
            "departments": [
                {
                    "department_name": "Bar",
                    "gl_code": "5000",
                    "rows": [
                        {
                            "normalized_name": "margarita",
                            "product_name": "Margarita",
                            "quantity": 3,
                        }
                    ],
                }
            ],
            "warnings": [],
        }
        resolved_map = {
            "margarita": {"status": "manual", "product_id": product.id}
        }

        (
            department_reports,
            overall_summary,
            warnings,
            overall_unmapped,
            overall_skipped,
        ) = _calculate_department_usage(payload, resolved_map, only_mapped=True)

        assert warnings == []
        assert overall_unmapped == []
        assert overall_skipped == []
        assert department_reports

        department_items = department_reports[0]["items"]
        assert department_items

        with_receiving = next(
            item
            for item in department_items
            if item["item_name"] == item_with_receiving.name
        )
        assert with_receiving["base_quantity"] == pytest.approx(6.0)
        assert (
            with_receiving["base_unit_label"]
            == get_unit_label(item_with_receiving.base_unit)
        )
        assert with_receiving["receiving_unit"] == "Case"
        assert with_receiving["receiving_quantity"] == pytest.approx(0.5)

        without_receiving = next(
            item
            for item in department_items
            if item["item_name"] == item_without_receiving.name
        )
        assert without_receiving["base_quantity"] == pytest.approx(1.5)
        assert (
            without_receiving["base_unit_label"]
            == get_unit_label(item_without_receiving.base_unit)
        )
        assert without_receiving["receiving_unit"] is None
        assert without_receiving["receiving_quantity"] is None

        overall_items = overall_summary["items"]
        overall_with_receiving = next(
            item
            for item in overall_items
            if item["item_name"] == item_with_receiving.name
        )
        assert overall_with_receiving["base_quantity"] == pytest.approx(6.0)
        assert overall_with_receiving["receiving_unit"] == "Case"
        assert overall_with_receiving["receiving_quantity"] == pytest.approx(0.5)

