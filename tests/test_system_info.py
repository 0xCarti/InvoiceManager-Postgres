import os

from flask import url_for
from werkzeug.security import generate_password_hash

from app import db
from app.models import User
from tests.utils import login


def test_admin_can_view_system_info(client, app):
    with app.app_context():
        with app.test_request_context():
            expected = url_for("admin.system_info")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        resp = client.get(expected, follow_redirects=True)
        assert resp.status_code == 200
        assert b"System Information" in resp.data


def test_non_admin_forbidden_from_system_info(client, app):
    with app.app_context():
        user = User(
            email="normal@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
    with client:
        login(client, "normal@example.com", "pass")
        resp = client.get("/controlpanel/system")
        assert resp.status_code == 403
