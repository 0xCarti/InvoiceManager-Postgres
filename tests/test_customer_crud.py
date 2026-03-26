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
