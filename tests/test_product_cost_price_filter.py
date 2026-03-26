from werkzeug.security import generate_password_hash

from app import db
from app.models import Product, User
from tests.utils import login


def setup_products(app):
    with app.app_context():
        user = User(
            email="costprice@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.add_all(
            [
                Product(name="Cheap", price=5, cost=1),
                Product(name="Medium", price=10, cost=5),
                Product(name="Expensive", price=20, cost=15),
            ]
        )
        db.session.commit()
        return user.email


def test_view_products_cost_and_price_filters(client, app):
    email = setup_products(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/products?cost_min=6&cost_max=16")
        assert b"Expensive" in resp.data
        assert b"Medium" not in resp.data
        assert b"Cheap" not in resp.data
        resp = client.get("/products?price_min=6&price_max=15")
        assert b"Medium" in resp.data
        assert b"Cheap" not in resp.data
        assert b"Expensive" not in resp.data


def test_view_products_invalid_ranges(client, app):
    email = setup_products(app)
    with client:
        login(client, email, "pass")
        resp = client.get(
            "/products?cost_min=10&cost_max=5", follow_redirects=True
        )
        assert b"Invalid cost range" in resp.data
        resp = client.get(
            "/products?price_min=20&price_max=10", follow_redirects=True
        )
        assert b"Invalid price range" in resp.data
