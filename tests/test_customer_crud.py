from werkzeug.security import generate_password_hash

from app import db
from app.models import Customer, User
from tests.utils import login


def setup_user(app):
    with app.app_context():
        user = User(
            email="cust@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.email


def test_customer_crud_flow(client, app):
    email = setup_user(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/customers")
        assert resp.status_code == 200
        assert client.get("/customers/create").status_code == 200
        resp = client.post(
            "/customers/create",
            data={
                "first_name": "Cust",
                "last_name": "Omer",
                "gst_exempt": "y",
                "pst_exempt": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        cust = Customer.query.filter_by(
            first_name="Cust", last_name="Omer"
        ).first()
        assert cust is not None
        cid = cust.id
    with client:
        login(client, email, "pass")
        assert client.get(f"/customers/{cid}/edit").status_code == 200
        resp = client.post(
            f"/customers/{cid}/edit",
            data={
                "first_name": "New",
                "last_name": "Customer",
                "gst_exempt": "",
                "pst_exempt": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        cust = db.session.get(Customer, cid)
        assert cust.first_name == "New"
        assert not cust.pst_exempt
    with client:
        login(client, email, "pass")
        assert client.get("/customers/999/edit").status_code == 404
        resp = client.post(f"/customers/{cid}/delete", follow_redirects=True)
        assert resp.status_code == 200
    with app.app_context():
        cust = db.session.get(Customer, cid)
        assert cust.archived


def test_create_customer_modal_returns_json_payload(client, app):
    email = setup_user(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/customers/create-modal",
            data={
                "first_name": "Modal",
                "last_name": "Customer",
                "gst_exempt": "y",
                "pst_exempt": "",
            },
        )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["customer"]["id"] > 0
    assert payload["customer"]["first_name"] == "Modal"
    assert payload["customer"]["last_name"] == "Customer"

    with app.app_context():
        cust = Customer.query.filter_by(
            first_name="Modal", last_name="Customer"
        ).first()
        assert cust is not None
