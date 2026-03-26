import pytest

from app import db
from app.models import Item, ItemUnit, Product, ProductRecipeItem
from app.utils.imports import _import_products


def test_import_products_with_recipe(tmp_path, app):
    csv_path = tmp_path / "prods.csv"
    with app.app_context():
        b = Item(name="Buns", base_unit="each")
        p = Item(name="Patties", base_unit="each")
        db.session.add_all([b, p])
        db.session.commit()
        bu = ItemUnit(
            item_id=b.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        pu = ItemUnit(
            item_id=p.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add_all([bu, pu])
        db.session.commit()

    csv_path.write_text(
        "name,price,cost,gl_code,recipe\nBurger,5,3,4000,Buns:2:each;Patties:1:each\n"
    )

    with app.app_context():
        count = _import_products(str(csv_path))
        assert count == 1
        prod = Product.query.filter_by(name="Burger").first()
        assert prod is not None
        items = {ri.item.name for ri in prod.recipe_items}
        assert items == {"Buns", "Patties"}
        qty_map = {ri.item.name: ri.quantity for ri in prod.recipe_items}
        unit_names = {ri.item.name: ri.unit.name for ri in prod.recipe_items}
        assert qty_map["Buns"] == 2
        assert qty_map["Patties"] == 1
        assert unit_names["Buns"] == "each"
        assert unit_names["Patties"] == "each"


def test_import_products_missing_item(tmp_path, app):
    csv_path = tmp_path / "prods.csv"
    csv_path.write_text(
        "name,price,cost,gl_code,recipe\nBurger,5,3,4000,Missing:1\n"
    )

    with app.app_context():
        with pytest.raises(ValueError):
            _import_products(str(csv_path))
        assert Product.query.count() == 0
