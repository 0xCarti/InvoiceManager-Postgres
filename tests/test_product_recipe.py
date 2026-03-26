from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    Invoice,
    Item,
    ItemUnit,
    Product,
    ProductRecipeItem,
    User,
)
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="chef@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        customer = Customer(first_name="Foo", last_name="Bar")
        item1 = Item(name="Flour", quantity=100, base_unit="gram")
        item2 = Item(name="Sugar", quantity=50, base_unit="gram")
        db.session.add_all([item1, item2])
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
        product = Product(name="Cake", price=5.0, cost=2.0, quantity=10)
        db.session.add_all([user, customer, product])
        db.session.commit()
        db.session.add_all(
            [
                ProductRecipeItem(
                    product_id=product.id,
                    item_id=item1.id,
                    unit_id=iu1.id,
                    quantity=2,
                    countable=True,
                ),
                ProductRecipeItem(
                    product_id=product.id,
                    item_id=item2.id,
                    unit_id=iu2.id,
                    quantity=1,
                    countable=False,
                ),
            ]
        )
        db.session.commit()
        return user.email, customer.id, product.name, item1.id, item2.id


def test_recipe_reduces_inventory_on_sale(client, app):
    email, cust_id, prod_name, item1_id, item2_id = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?3??"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        item1 = db.session.get(Item, item1_id)
        item2 = db.session.get(Item, item2_id)
        product = Product.query.filter_by(name=prod_name).first()
        assert item1.quantity == 100 - 6
        assert item2.quantity == 50 - 3
        assert product.quantity == 10 - 3
        inv = Invoice.query.first()
        assert inv is not None
