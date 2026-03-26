from werkzeug.security import generate_password_hash

from app import db
from app.models import Customer, User
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="custfilter@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        c1 = Customer(first_name="Alice", last_name="Smith", gst_exempt=True, pst_exempt=False)
        c2 = Customer(first_name="Bob", last_name="Brown", gst_exempt=False, pst_exempt=True)
        db.session.add_all([user, c1, c2])
        db.session.commit()
        return user.email


def test_view_customers_filter_by_name(client, app):
    email = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/customers?name_query=Alice")
        assert resp.status_code == 200
        assert b"Alice Smith" in resp.data
        assert b"Bob Brown" not in resp.data


def test_view_customers_filter_by_gst(client, app):
    email = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/customers?gst_exempt=yes")
        assert resp.status_code == 200
        assert b"Alice Smith" in resp.data
        assert b"Bob Brown" not in resp.data
