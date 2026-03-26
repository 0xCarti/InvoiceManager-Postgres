from werkzeug.security import generate_password_hash

from app import db
from app.models import Location, User
from tests.utils import login


def test_admin_invite_creates_user(client, app, monkeypatch):
    sent = {}

    class DummySMTP:
        def __init__(self, host, port):
            sent["host"] = host
            sent["port"] = port

        def starttls(self):
            sent["tls"] = True

        def login(self, u, p):
            sent["login"] = (u, p)

        def send_message(self, msg):
            sent["message"] = msg

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr("app.utils.email.smtplib.SMTP", DummySMTP)

    with app.app_context():
        admin = User(
            email="admin2@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()

    login(client, "admin2@example.com", "adminpass")
    client.post(
        "/controlpanel/users",
        data={"email": "new@example.com", "submit": True},
        follow_redirects=True,
    )

    with app.app_context():
        user = User.query.filter_by(email="new@example.com").first()
        assert user is not None
        assert not user.active

    assert "message" in sent


def test_login_inactive_user(client, app):
    with app.app_context():
        user = User(
            email="inactive@example.com",
            password=generate_password_hash("password"),
            active=False,
        )
        db.session.add(user)
        db.session.commit()

    response = login(client, "inactive@example.com", "password")
    assert response.status_code == 200
    assert b"Please contact system admin to activate account." in response.data


def test_add_location(client, app):
    with app.app_context():
        user = User(
            email="loc@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

    # Login and add location within the same client context
    with client:
        login(client, "loc@example.com", "pass")
        response = client.post(
            "/locations/add",
            data={"name": "Warehouse", "is_spoilage": "y"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        location = Location.query.filter_by(name="Warehouse").first()
        assert location is not None
        assert location.is_spoilage
