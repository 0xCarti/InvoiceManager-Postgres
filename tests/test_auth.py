from flask import url_for
from werkzeug.security import generate_password_hash

from app import db
from app.models import User


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
