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


def test_login_page_renders_password_toggle(client):
    response = client.get("/auth/login")

    assert response.status_code == 200
    assert b'data-password-toggle-group' in response.data
    assert response.data.count(b'aria-pressed="false"') >= 1
    assert b'js/password_toggle.js' in response.data


def test_zero_threat_verification_page_is_get_only(client):
    get_response = client.get("/zero-threat.html")
    assert get_response.status_code == 200
    assert b"zeroThreat=" in get_response.data

    post_response = client.post("/zero-threat.html")
    assert post_response.status_code == 405


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
            invitation_pending=True,
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
                "new_password": "new-invite-password",
                "confirm_password": "new-invite-password",
            },
            follow_redirects=True,
        )

    assert reset_response.status_code == 200
    assert b"Password updated." in reset_response.data

    login_response = login(client, "invited@example.com", "new-invite-password")
    assert login_response.status_code == 200
    assert b"Please contact system admin to activate account." not in login_response.data

    with app.app_context():
        refreshed = User.query.filter_by(email="invited@example.com").first()
        assert refreshed is not None
        assert refreshed.active is True
        assert refreshed.invitation_pending is False


def test_reset_token_page_renders_password_toggles(client, app):
    with app.app_context():
        user = User(
            email="toggle-reset@example.com",
            password=generate_password_hash("temporary"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        token = generate_reset_token(user)

    response = client.get(f"/auth/reset/{token}")

    assert response.status_code == 200
    assert response.data.count(b'data-password-toggle-group') == 2
    assert response.data.count(b'aria-pressed="false"') == 2
    assert b'js/password_toggle.js' in response.data


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


def test_invited_user_password_policy_rejects_short_and_oversized_passwords(
    client, app
):
    with app.app_context():
        user = User(
            email="invite-password-policy@example.com",
            password=generate_password_hash("temporary-password"),
            active=False,
            invitation_pending=True,
        )
        db.session.add(user)
        db.session.commit()
        original_password_hash = user.password
        token = generate_reset_token(user)

    with client:
        reset_page = client.get(f"/auth/reset/{token}")
        csrf_token = extract_csrf_token(reset_page)
        short_response = client.post(
            f"/auth/reset/{token}",
            data={
                "csrf_token": csrf_token,
                "new_password": "too-short",
                "confirm_password": "too-short",
            },
        )
        oversized_password = "x" * 129
        oversized_response = client.post(
            f"/auth/reset/{token}",
            data={
                "csrf_token": csrf_token,
                "new_password": oversized_password,
                "confirm_password": oversized_password,
            },
        )

    assert short_response.status_code == 200
    assert b"Password must be at least 12 characters." in short_response.data
    assert oversized_response.status_code == 200
    assert (
        b"Password must be 128 characters or fewer." in oversized_response.data
    )
    with app.app_context():
        refreshed = User.query.filter_by(
            email="invite-password-policy@example.com"
        ).one()
        assert refreshed.password == original_password_hash
        assert refreshed.active is False
        assert refreshed.invitation_pending is True


def test_password_reset_does_not_reactivate_inactive_non_invited_user(
    client, app
):
    with app.app_context():
        user = User(
            email="archived-never-logged-in@example.com",
            password=generate_password_hash("old-password"),
            active=False,
            invitation_pending=False,
        )
        db.session.add(user)
        db.session.commit()
        token = generate_reset_token(user)

    with client:
        reset_page = client.get(f"/auth/reset/{token}")
        csrf_token = extract_csrf_token(reset_page)
        response = client.post(
            f"/auth/reset/{token}",
            data={
                "csrf_token": csrf_token,
                "new_password": "new-password",
                "confirm_password": "new-password",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Password updated." in response.data
    with app.app_context():
        user = User.query.filter_by(
            email="archived-never-logged-in@example.com"
        ).one()
        assert user.active is False
        assert user.invitation_pending is False
