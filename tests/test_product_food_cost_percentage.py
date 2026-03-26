from werkzeug.security import generate_password_hash

from app import db
from app.models import Product, User
from tests.utils import login


def test_food_cost_percentage_display(client, app):
    """Product list displays calculated food cost percentage."""
    with app.app_context():
        user = User(
            email="foodcost@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        product = Product(name="Sandwich", price=10.0, cost=4.0)
        db.session.add_all([user, product])
        db.session.commit()
    with client:
        login(client, "foodcost@example.com", "pass")
        resp = client.get("/products")
        assert resp.status_code == 200
        assert b"40.00%" in resp.data
