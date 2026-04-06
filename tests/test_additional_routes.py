import os

import pytest
from flask import url_for
from werkzeug.security import generate_password_hash

from app import db
from app.models import Location, Permission, PermissionGroup, Transfer, User
from tests.utils import extract_csrf_token, login


def _grant_permissions(user, *permission_codes):
    group = PermissionGroup(
        name=f"Test Group {user.email}",
        description="Test permission group.",
    )
    group.permissions = Permission.query.filter(
        Permission.code.in_(permission_codes)
    ).all()
    db.session.add(group)
    db.session.flush()
    user.permission_groups = [group]
    user.invalidate_permission_cache()


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
        token_page = client.get("/controlpanel/users")
        csrf_token = extract_csrf_token(token_page)
        response = client.post(
            f"/activate_user/{target_id}",
            data={"csrf_token": csrf_token},
        )
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
        db.session.add(user)
        _grant_permissions(user, "transfers.view")
        loc1 = Location(name="FromLoc")
        loc2 = Location(name="ToLoc")
        db.session.add_all([loc1, loc2])
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


def test_view_transfers_filters_from_and_to_location_together(client, app):
    with app.app_context():
        user = User(
            email="transferfilters@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        _grant_permissions(user, "transfers.view")
        from_loc = Location(name="Warehouse A")
        to_loc = Location(name="Front Stand")
        other_to_loc = Location(name="Overflow")
        db.session.add_all([from_loc, to_loc, other_to_loc])
        db.session.commit()

        matching_transfer = Transfer(
            from_location_id=from_loc.id,
            to_location_id=to_loc.id,
            user_id=user.id,
        )
        other_transfer = Transfer(
            from_location_id=from_loc.id,
            to_location_id=other_to_loc.id,
            user_id=user.id,
        )
        db.session.add_all([matching_transfer, other_transfer])
        db.session.commit()
        matching_transfer_id = matching_transfer.id
        other_transfer_row_id = f"transfer-row-{other_transfer.id}"

    with client:
        login(client, "transferfilters@example.com", "pass")
        response = client.get(
            "/transfers?filter=all&from_location=Warehouse&to_location=Front",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert str(matching_transfer_id).encode() in response.data
        assert other_transfer_row_id.encode() not in response.data
