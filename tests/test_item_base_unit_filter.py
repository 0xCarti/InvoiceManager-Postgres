from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, User
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="unitfilter@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        for i in range(21):
            db.session.add(Item(name=f"A{i}", base_unit="each"))
        db.session.add(Item(name="B0", base_unit="gram"))
        db.session.commit()
        return user.email


def test_view_items_filter_by_base_unit(client, app):
    email = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/items?base_unit=each")
        assert resp.status_code == 200
        assert b"A0" in resp.data
        assert b"B0" not in resp.data
        assert b"Filtering by Base Unit" in resp.data
        assert b"base_unit=each" in resp.data
