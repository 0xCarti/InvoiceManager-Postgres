from werkzeug.security import generate_password_hash

from app import db
from app.models import User
from app.routes.auth_routes import generate_reset_token
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

    response = client.post(
        "/auth/login",
        data={"email": "test@example.com", "password": "password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/profile")


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

        profile_page = client.get("/auth/profile")
        token = extract_csrf_token(profile_page)
        post_response = client.post(
            "/auth/logout",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        assert post_response.status_code == 302
        assert post_response.headers["Location"].endswith("/auth/login")


def test_login_is_case_insensitive(client, app):
    with app.app_context():
        user = User(
            email="MixedCase@example.com",
            password=generate_password_hash("password"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

    response = client.post(
        "/auth/login",
        data={"email": "mixedcase@example.com", "password": "password"},
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/auth/profile")


def test_invited_user_can_sign_in_after_setting_password(client, app):
    with app.app_context():
        user = User(
            email="invited@example.com",
            password=generate_password_hash("temporary"),
            active=False,
        )
        db.session.add(user)
        db.session.commit()
        token = generate_reset_token(user)

    with client:
        reset_page = client.get(f"/auth/reset/{token}")
        csrf_token = extract_csrf_token(reset_page)
        reset_response = client.post(
            f"/auth/reset/{token}",
            data={
                "csrf_token": csrf_token,
                "new_password": "newpass123",
                "confirm_password": "newpass123",
            },
            follow_redirects=True,
        )

    assert reset_response.status_code == 200
    assert b"Password updated." in reset_response.data

    login_response = login(client, "invited@example.com", "newpass123")
    assert login_response.status_code == 200
    assert b"Please contact system admin to activate account." not in login_response.data

    with app.app_context():
        refreshed = User.query.filter_by(email="invited@example.com").first()
        assert refreshed is not None
        assert refreshed.active is True


def test_inactive_user_is_logged_out_on_next_request(client, app):
    with app.app_context():
        user = User(
            email="deactivate@example.com",
            password=generate_password_hash("password"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

    with client:
        login(client, "deactivate@example.com", "password")

        with app.app_context():
            stored_user = User.query.filter_by(email="deactivate@example.com").first()
            stored_user.active = False
            db.session.commit()

        response = client.get("/auth/profile", follow_redirects=False)

    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]
