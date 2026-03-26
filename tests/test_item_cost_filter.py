from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, User
from tests.utils import login


def setup_items(app):
    with app.app_context():
        user = User(
            email="costfilter@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.add_all(
            [
                Item(name="Cheap", base_unit="each", cost=5),
                Item(name="Mid", base_unit="each", cost=10),
                Item(name="Expensive", base_unit="each", cost=20),
            ]
        )
        db.session.commit()
        return user.email


def test_view_items_filter_by_cost(client, app):
    email = setup_items(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/items?cost_min=11")
        assert b"Expensive" in resp.data
        assert b"Mid" not in resp.data
        assert b"Cheap" not in resp.data
        resp = client.get("/items?cost_max=10")
        assert b"Cheap" in resp.data
        assert b"Expensive" not in resp.data


def test_view_items_cost_invalid_range(client, app):
    email = setup_items(app)
    with client:
        login(client, email, "pass")
        resp = client.get(
            "/items?cost_min=15&cost_max=10", follow_redirects=True
        )
        assert b"Invalid cost range" in resp.data
