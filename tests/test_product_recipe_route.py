from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, ItemUnit, Product, ProductRecipeItem, User
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="precipe@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        item = Item(name="Flour", base_unit="gram")
        db.session.add_all([user, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        product = Product(name="Cake", price=5.0, cost=2.0)
        db.session.add_all([unit, product])
        db.session.commit()
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=unit.id,
                quantity=1,
                countable=True,
            )
        )
        db.session.commit()
        return user.email, product.id, item.id, unit.id


def test_edit_product_recipe_route(client, app):
    email, pid, item_id, unit_id = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.get(f"/products/{pid}/recipe")
        assert resp.status_code == 200
        resp = client.post(
            f"/products/{pid}/recipe",
            data={
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
                "items-0-countable": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        prod = db.session.get(Product, pid)
        assert prod.recipe_items[0].quantity == 3
