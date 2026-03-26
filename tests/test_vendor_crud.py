from werkzeug.security import generate_password_hash

from app import db
from app.models import User, Vendor
from tests.utils import login


def setup_user(app):
    with app.app_context():
        user = User(
            email="vend@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.email


def test_vendor_crud_flow(client, app):
    email = setup_user(app)
    with client:
        login(client, email, "pass")
        assert client.get("/vendors").status_code == 200
        assert client.get("/vendors/create").status_code == 200
        resp = client.post(
            "/vendors/create",
            data={
                "first_name": "Vend",
                "last_name": "Or",
                # Checked means charge tax. Leave PST unchecked to mark exempt.
                "gst_exempt": "y",
                "pst_exempt": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        vendor = Vendor.query.filter_by(
            first_name="Vend", last_name="Or"
        ).first()
        assert vendor is not None
        vid = vendor.id

    with client:
        login(client, email, "pass")
        assert client.get(f"/vendors/{vid}/edit").status_code == 200
        resp = client.post(
            f"/vendors/{vid}/edit",
            data={
                "first_name": "New",
                "last_name": "Vendor",
                # Unchecked = GST exempt, checked = charge PST
                "gst_exempt": "",
                "pst_exempt": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        vendor = db.session.get(Vendor, vid)
        assert vendor.first_name == "New"
        # PST checkbox was checked so vendor should not be PST exempt
        assert not vendor.pst_exempt

    with client:
        login(client, email, "pass")
        assert client.get("/vendors/999/edit").status_code == 404
        resp = client.post(f"/vendors/{vid}/delete", follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        vendor = db.session.get(Vendor, vid)
        assert vendor.archived


def test_view_vendors(client, app):
    email = setup_user(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/vendors")
        assert resp.status_code == 200
