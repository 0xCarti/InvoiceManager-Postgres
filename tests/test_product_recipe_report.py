from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, ItemUnit, Product, ProductRecipeItem, User
from tests.utils import login


def setup_products(app):
    with app.app_context():
        user = User(
            email="rep@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        item1 = Item(name="Flour", base_unit="gram", cost=0.5)
        item2 = Item(name="Sugar", base_unit="gram", cost=0.25)
        db.session.add_all([user, item1, item2])
        db.session.commit()
        iu1 = ItemUnit(
            item_id=item1.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        iu2 = ItemUnit(
            item_id=item2.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add_all([iu1, iu2])
        db.session.commit()
        prod1 = Product(name="Cake", price=5.0, cost=3.0)
        prod2 = Product(name="Pie", price=4.0, cost=2.0)
        db.session.add_all([prod1, prod2])
        db.session.commit()
        db.session.add_all(
            [
                ProductRecipeItem(
                    product_id=prod1.id,
                    item_id=item1.id,
                    unit_id=iu1.id,
                    quantity=2,
                ),
                ProductRecipeItem(
                    product_id=prod1.id,
                    item_id=item2.id,
                    unit_id=iu2.id,
                    quantity=1,
                ),
                ProductRecipeItem(
                    product_id=prod2.id,
                    item_id=item1.id,
                    unit_id=iu1.id,
                    quantity=3,
                ),
            ]
        )
        db.session.commit()
        return prod1.id, prod2.id


def test_recipe_report_select_all(client, app):
    p1, p2 = setup_products(app)
    login(client, "rep@example.com", "pass")
    resp = client.post(
        "/reports/product-recipes",
        data={"select_all": "y"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Cake" in resp.data and b"Pie" in resp.data
    assert b"Flour" in resp.data and b"Sugar" in resp.data
    assert (
        b"$1.00" in resp.data
        and b"$0.25" in resp.data
        and b"$1.50" in resp.data
    )


def test_recipe_report_specific_products(client, app):
    p1, p2 = setup_products(app)
    login(client, "rep@example.com", "pass")
    resp = client.post(
        "/reports/product-recipes",
        data={"products": [str(p1)]},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Cake" in resp.data
    assert b"Pie" not in resp.data
    assert b"$1.00" in resp.data and b"$0.25" in resp.data
