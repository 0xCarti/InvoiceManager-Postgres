import os

import pytest
from flask import url_for
from werkzeug.security import generate_password_hash

from app import db
from app.models import Location, Transfer, User
from tests.utils import login


def test_admin_can_activate_user(client, app):
    with app.app_context():
        user = User(
            email="inactive2@example.com",
            password=generate_password_hash("pass"),
            active=False,
        )
        db.session.add(user)
        db.session.commit()
        target_id = user.id
        with app.test_request_context():
            expected = url_for("admin.users")

    with client:
        admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
        admin_pass = os.getenv("ADMIN_PASS", "adminpass")
        login(client, admin_email, admin_pass)
        response = client.get(f"/activate_user/{target_id}")
        assert response.status_code == 302
        assert response.headers["Location"].endswith(expected)

    with app.app_context():
        updated = db.session.get(User, target_id)
        assert updated.active


def test_view_transfers(client, app):
    with app.app_context():
        user = User(
            email="view@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc1 = Location(name="FromLoc")
        loc2 = Location(name="ToLoc")
        db.session.add_all([user, loc1, loc2])
        db.session.commit()
        transfer = Transfer(
            from_location_id=loc1.id, to_location_id=loc2.id, user_id=user.id
        )
        db.session.add(transfer)
        db.session.commit()
        tid = transfer.id

    with client:
        login(client, "view@example.com", "pass")
        response = client.get("/transfers", follow_redirects=True)
        assert response.status_code == 200
        assert str(tid).encode() in response.data
