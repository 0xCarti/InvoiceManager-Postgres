import re

from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import User


def test_password_reset_flow(client, app, monkeypatch):
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
        user = User(
            email="reset@example.com",
            password=generate_password_hash("old"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

    client.post(
        "/auth/reset",
        data={"email": "reset@example.com"},
        follow_redirects=True,
    )
    assert "message" in sent
    m = re.search(r"/auth/reset/([^\s]+)", sent["message"].get_content())
    token = m.group(1)

    client.post(
        f"/auth/reset/{token}",
        data={"new_password": "newpass", "confirm_password": "newpass"},
        follow_redirects=True,
    )

    with app.app_context():
        user = User.query.filter_by(email="reset@example.com").first()
        assert check_password_hash(user.password, "newpass")


def test_login_page_has_reset_link(client):
    response = client.get("/auth/login")
    assert b'href="/auth/reset"' in response.data


def test_password_reset_unknown_email(client):
    response = client.post(
        "/auth/reset",
        data={"email": "missing@example.com"},
        follow_redirects=True,
    )
    assert b"No account found with that email." in response.data
