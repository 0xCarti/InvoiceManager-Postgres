import os

from flask import url_for
from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import Customer, Invoice, Location, Product, Transfer, User
from tests.utils import login


def create_user(app, email="user@example.com"):
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("oldpass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_user_can_change_password(client, app):
    create_user(app, "profile@example.com")
    with client:
        login(client, "profile@example.com", "oldpass")
        resp = client.post(
            "/auth/profile",
            data={
                "current_password": "oldpass",
                "new_password": "newpass",
                "confirm_password": "newpass",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        user = User.query.filter_by(email="profile@example.com").first()
        assert check_password_hash(user.password, "newpass")


def test_admin_view_and_change_user_password(client, app):
    user_id = create_user(app, "target@example.com")
    with app.app_context():
        loc = Location(name="L")
        db.session.add(loc)
        db.session.commit()
        transfer = Transfer(
            from_location_id=loc.id, to_location_id=loc.id, user_id=user_id
        )
        cust = Customer(first_name="A", last_name="B")
        prod = Product(name="P", price=1.0, cost=0.5)
        db.session.add_all([transfer, cust, prod])
        db.session.commit()
        inv = Invoice(id="INVX", user_id=user_id, customer_id=cust.id)
        db.session.add(inv)
        db.session.commit()
        transfer_id = transfer.id

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        resp = client.get(f"/user_profile/{user_id}", follow_redirects=True)
        assert resp.status_code == 200
        assert b"INVX" in resp.data
        assert str(transfer_id).encode() in resp.data

        resp = client.post(
            f"/user_profile/{user_id}",
            data={"new_password": "changed", "confirm_password": "changed"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        updated = db.session.get(User, user_id)
        assert check_password_hash(updated.password, "changed")


def test_admin_users_page_links_to_profile(client, app):
    with app.app_context():
        user = User(
            email="link@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        uid = user.id
        with app.test_request_context():
            profile_url = url_for("admin.user_profile", user_id=uid)

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        resp = client.get("/controlpanel/users", follow_redirects=True)
        assert resp.status_code == 200
        assert profile_url.encode() in resp.data


def test_user_can_set_timezone(client, app):
    create_user(app, "tz@example.com")
    with client:
        login(client, "tz@example.com", "oldpass")
        resp = client.post(
            "/auth/profile",
            data={"timezone": "US/Eastern"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        user = User.query.filter_by(email="tz@example.com").first()
        assert user.timezone == "US/Eastern"


