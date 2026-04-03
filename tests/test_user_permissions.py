import os

from werkzeug.security import generate_password_hash

from app import db
from app.models import PermissionGroup, User
from app.permissions import SYSTEM_FULL_ACCESS_GROUP_KEY
from tests.utils import login


def test_new_user_receives_default_full_access_group(app):
    with app.app_context():
        user = User(
            email="defaultgroup@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

        db.session.refresh(user)
        assert any(
            group.key == SYSTEM_FULL_ACCESS_GROUP_KEY
            for group in user.permission_groups
        )


def test_user_without_permission_groups_is_redirected_to_profile_and_blocked_from_restricted_routes(
    client, app
):
    with app.app_context():
        user = User(
            email="restricted@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        user.permission_groups = []
        user.invalidate_permission_cache()
        db.session.commit()

    with client:
        response = login(client, "restricted@example.com", "pass")
        assert response.request.path == "/auth/profile"

        restricted_page = client.get("/purchase_orders")
        assert restricted_page.status_code == 403

        profile_page = client.get("/auth/profile")
        assert profile_page.status_code == 200
        assert b"Purchase Orders" not in profile_page.data


def test_permission_group_pages_follow_permissions(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        limited = User(
            email="limited-perms@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(limited)
        db.session.commit()
        limited.permission_groups = []
        limited.invalidate_permission_cache()
        db.session.commit()

        users_group = PermissionGroup(name="User Management")
        db.session.add(users_group)
        db.session.commit()
        limited.permission_groups = [users_group]
        limited.invalidate_permission_cache()
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        admin_response = client.get("/controlpanel/permission-groups")
        assert admin_response.status_code == 200

    with client:
        login(client, "limited-perms@example.com", "pass")
        limited_response = client.get("/controlpanel/permission-groups")
        assert limited_response.status_code == 403
