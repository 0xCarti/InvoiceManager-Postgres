from werkzeug.security import generate_password_hash

from app import db
from app.models import Product, Setting, User
from tests.permission_helpers import make_super_admin
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
        make_super_admin(user)
    with client:
        login(client, "foodcost@example.com", "pass")
        resp = client.get("/products")
        assert resp.status_code == 200
        assert b"40.00%" in resp.data


def test_food_cost_percentage_removes_included_tax(client, app):
    """Product food cost percentage uses price before configured tax."""
    with app.app_context():
        user = User(
            email="foodcost-tax@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        product = Product(name="Pretzel", price=9.0, cost=3.0)
        Setting.set_food_cost_tax_rate(5)
        db.session.add_all([user, product])
        db.session.commit()
        make_super_admin(user)
    with client:
        login(client, "foodcost-tax@example.com", "pass")
        resp = client.get("/products")
        assert resp.status_code == 200
        assert b"35.00%" in resp.data
