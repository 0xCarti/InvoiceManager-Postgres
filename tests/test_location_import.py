import pytest

from app import db
from app.models import Location, Product
from app.utils.imports import _import_locations


def test_import_locations_with_products(tmp_path, app):
    csv_path = tmp_path / "locs.csv"
    with app.app_context():
        p1 = Product(name="Burger", price=1.0, cost=0.5)
        p2 = Product(name="Fries", price=1.0, cost=0.5)
        db.session.add_all([p1, p2])
        db.session.commit()

    csv_path.write_text("name,products\nWarehouse,Burger;Fries\n")

    with app.app_context():
        count = _import_locations(str(csv_path))
        assert count == 1
        loc = Location.query.filter_by(name="Warehouse").first()
        assert loc is not None
        assert {p.name for p in loc.products} == {"Burger", "Fries"}


def test_import_locations_missing_product(tmp_path, app):
    csv_path = tmp_path / "locs.csv"
    with app.app_context():
        db.session.query(Product).delete()
        db.session.add(Product(name="Burger", price=1.0, cost=0.5))
        db.session.commit()

    csv_path.write_text("name,products\nWarehouse,Burger;Missing\n")

    with app.app_context():
        with pytest.raises(ValueError):
            _import_locations(str(csv_path))
        assert Location.query.count() == 0
