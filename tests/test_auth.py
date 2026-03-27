from flask import url_for
from werkzeug.security import generate_password_hash

from app import db
from app.models import User
from tests.utils import extract_csrf_token, login


def test_login_redirect(client, app):
    with app.app_context():
        user = User(
            email="test@example.com",
            password=generate_password_hash("password"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        with app.test_request_context():
            expected = url_for("transfer.view_transfers")

    response = client.post(
        "/auth/login",
        data={"email": "test@example.com", "password": "password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(expected)


def test_logout_requires_post(client, app):
    with app.app_context():
        user = User(
            email="logout@example.com",
            password=generate_password_hash("password"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

    with client:
        login(client, "logout@example.com", "password")
        get_response = client.get("/auth/logout")
        assert get_response.status_code == 405

        home_page = client.get("/")
        token = extract_csrf_token(home_page)
        post_response = client.post(
            "/auth/logout",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert post_response.status_code == 302
        assert post_response.headers["Location"].endswith("/auth/login")
