from werkzeug.security import check_password_hash, generate_password_hash

from app import db
from app.models import Location, Permission, PermissionGroup, User
from tests.utils import login


def test_admin_invite_creates_user(client, app, monkeypatch):
    sent = {}

    class DummySMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout

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
        assert user.invitation_pending is True

    assert "message" in sent


def test_admin_invite_does_not_create_user_when_email_send_fails(
    client, app, monkeypatch
):
    class DummySMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host
            self.port = port
            self.timeout = timeout

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            raise TimeoutError("SMTP request timed out")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr("app.utils.email.smtplib.SMTP", DummySMTP)

    with app.app_context():
        admin = User(
            email="admin-invite-fail@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()

    with client:
        login(client, "admin-invite-fail@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={"email": "invite-fail@example.com", "submit": True},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"Unable to send invitation email. Please verify SMTP settings and try again."
        in response.data
    )

    with app.app_context():
        assert User.query.filter_by(email="invite-fail@example.com").first() is None


def test_admin_invite_invalid_email_shows_form_error_not_user_not_found(
    client, app, monkeypatch
):
    class DummySMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host
            self.port = port
            self.timeout = timeout

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            raise AssertionError("Invite email should not be sent for invalid input")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr("app.utils.email.smtplib.SMTP", DummySMTP)

    with app.app_context():
        admin = User(
            email="admin-invalid@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()

    with client:
        login(client, "admin-invalid@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={"email": "not-an-email", "submit": True},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Invalid email address." in response.data
    assert b"User not found" not in response.data

    with app.app_context():
        user = User.query.filter_by(email="not-an-email").first()
        assert user is None


def test_admin_invite_treats_email_as_case_insensitive(
    client, app, monkeypatch
):
    class DummySMTP:
        def __init__(self, host, port, timeout=None):
            self.host = host
            self.port = port
            self.timeout = timeout

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            raise AssertionError("Duplicate invite should not send an email")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            pass

    monkeypatch.setattr("app.utils.email.smtplib.SMTP", DummySMTP)

    with app.app_context():
        admin = User(
            email="admin-case@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        existing = User(
            email="Demo@Example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([admin, existing])
        db.session.commit()

    with client:
        login(client, "admin-case@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={"email": "demo@example.com", "submit": True},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"User already exists. Use password reset if they need a new setup email."
        in response.data
    )

    with app.app_context():
        assert User.query.filter_by(email="demo@example.com").first() is None


def test_admin_can_resend_pending_invite_and_update_groups(
    client, app, monkeypatch
):
    sent = {}

    class DummySMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host
            sent["port"] = port
            sent["timeout"] = timeout

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
            email="admin-resend@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        pending_user = User(
            email="pending@example.com",
            password=generate_password_hash("old-temp"),
            active=False,
            invitation_pending=True,
        )
        permission = Permission.query.filter_by(code="transfers.view").one()
        group = PermissionGroup(name="Transfer Viewers")
        group.permissions = [permission]
        db.session.add_all([admin, pending_user, group])
        db.session.commit()
        old_password_hash = pending_user.password
        group_id = group.id

    with client:
        login(client, "admin-resend@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={
                "email": "pending@example.com",
                "group_ids": [str(group_id)],
                "submit": True,
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Invitation re-sent." in response.data
    assert "message" in sent
    assert sent["message"]["To"] == "pending@example.com"

    with app.app_context():
        users = User.query.filter_by(email="pending@example.com").all()
        assert len(users) == 1
        pending_user = users[0]
        assert pending_user.password != old_password_hash
        assert pending_user.active is False
        assert [group.name for group in pending_user.permission_groups] == [
            "Transfer Viewers"
        ]


def test_pending_invite_can_be_deleted_from_user_list(client, app):
    with app.app_context():
        admin = User(
            email="admin-delete-invite@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        pending_user = User(
            email="delete-invite@example.com",
            password=generate_password_hash("temp-pass"),
            active=False,
            invitation_pending=True,
        )
        db.session.add_all([admin, pending_user])
        db.session.commit()
        pending_user_id = pending_user.id

    with client:
        login(client, "admin-delete-invite@example.com", "adminpass")
        response = client.post(
            f"/delete_user/{pending_user_id}",
            data={},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Pending invite deleted." in response.data

    with app.app_context():
        assert db.session.get(User, pending_user_id) is None


def test_pending_invite_cannot_be_manually_activated(client, app):
    with app.app_context():
        admin = User(
            email="admin-pending-guard@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        pending_user = User(
            email="guarded-pending@example.com",
            password=generate_password_hash("temp-pass"),
            active=False,
            invitation_pending=True,
        )
        db.session.add_all([admin, pending_user])
        db.session.commit()
        pending_user_id = pending_user.id

    with client:
        login(client, "admin-pending-guard@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={"user_id": pending_user_id, "action": "toggle_active"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"Pending invites cannot be activated manually. Re-send or delete the invite instead."
        in response.data
    )

    with app.app_context():
        pending_user = db.session.get(User, pending_user_id)
        assert pending_user is not None
        assert pending_user.active is False


def test_admin_can_assign_display_name_used_in_communication_user_lists(client, app):
    with app.app_context():
        user = User(
            email="named-user@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            f"/controlpanel/users/{user_id}/access",
            data={
                "access-display_name": "Casey Crew",
                "access-submit": "1",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Casey Crew" in response.data

        communications_page = client.get("/communications", follow_redirects=True)
        assert communications_page.status_code == 200
        assert b"Casey Crew (named-user@example.com)" in communications_page.data

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user is not None
        assert user.display_name == "Casey Crew"


def test_admin_can_assign_permission_group_to_super_admin_via_access_page(client, app):
    with app.app_context():
        metabase_permission = Permission.query.filter_by(code="reports.metabase").first()
        assert metabase_permission is not None

        group = PermissionGroup(
            name="Metabase Operators",
            description="Access to the Metabase link.",
        )
        group.permissions = [metabase_permission]

        target_user = User(
            email="metabase-admin@example.com",
            password=generate_password_hash("pass"),
            active=True,
            is_admin=True,
        )
        db.session.add_all([group, target_user])
        db.session.commit()
        target_user_id = target_user.id
        group_id = group.id

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            f"/controlpanel/users/{target_user_id}/access",
            data={
                "access-display_name": "Ops Admin",
                "access-group_ids": [str(group_id)],
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"User access updated." in response.data
    assert b"Ops Admin" in response.data
    assert b"Metabase Operators" in response.data

    with app.app_context():
        target_user = db.session.get(User, target_user_id)
        assert target_user is not None
        assert target_user.display_name == "Ops Admin"
        assert [group.name for group in target_user.permission_groups] == [
            "Metabase Operators"
        ]


def test_admin_users_page_shows_invite_form_guidance(client, app):
    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.get("/controlpanel/users", follow_redirects=True)

    assert response.status_code == 200
    assert (
        b"Choose an emailed setup link or create an active account with a password."
        in response.data
    )
    assert b"Manual-password accounts do not send any email." in response.data
    assert b"data-manual-password-fields" in response.data
    assert b"data-add-user-submit" in response.data
    assert b'input[name="creation_method"]' in response.data


def test_admin_can_send_password_reset_from_user_list(client, app, monkeypatch):
    sent = {}

    def fake_send_email(to_address, subject, body):
        sent.update(to_address=to_address, subject=subject, body=body)

    monkeypatch.setattr("app.routes.auth_routes.send_email", fake_send_email)

    with app.app_context():
        user = User(
            email="reset-from-admin@example.com",
            password=generate_password_hash("current-password"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        original_password_hash = user.password

    with client:
        login(client, "admin@example.com", "adminpass")
        page = client.get("/controlpanel/users")
        assert page.status_code == 200
        assert b'value="send_password_reset"' in page.data
        assert b"Send Password Reset" in page.data

        response = client.post(
            "/controlpanel/users",
            data={"user_id": user_id, "action": "send_password_reset"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"Password reset email sent to reset-from-admin@example.com."
        in response.data
    )
    assert sent["to_address"] == "reset-from-admin@example.com"
    assert sent["subject"] == "Password Reset"
    assert "/reset/" in sent["body"]

    with app.app_context():
        assert db.session.get(User, user_id).password == original_password_hash


def test_admin_password_reset_send_failure_is_reported(client, app, monkeypatch):
    def fail_send_email(*args, **kwargs):
        raise TimeoutError("SMTP request timed out")

    monkeypatch.setattr("app.routes.auth_routes.send_email", fail_send_email)

    with app.app_context():
        user = User(
            email="reset-send-failure@example.com",
            password=generate_password_hash("current-password"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={"user_id": user_id, "action": "send_password_reset"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"Unable to send password reset email. Please verify SMTP settings and try again."
        in response.data
    )
    assert b"Password reset email sent to" not in response.data


def test_pending_invite_keeps_resend_invitation_action(client, app, monkeypatch):
    def unexpected_send_email(*args, **kwargs):
        raise AssertionError("Pending user should use the invitation email flow")

    monkeypatch.setattr(
        "app.routes.auth_routes.send_email", unexpected_send_email
    )

    with app.app_context():
        user = User(
            email="pending-reset@example.com",
            password=generate_password_hash("temporary-password"),
            active=False,
            invitation_pending=True,
        )
        db.session.add(user)
        db.session.commit()
        user_id = user.id

    with client:
        login(client, "admin@example.com", "adminpass")
        page = client.get("/controlpanel/users")
        assert page.status_code == 200
        assert b"Re-send Invite" in page.data
        assert (
            b"Send a password reset email to pending-reset@example.com?"
            not in page.data
        )

        response = client.post(
            "/controlpanel/users",
            data={"user_id": user_id, "action": "send_password_reset"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"Pending users should receive a re-sent invitation instead."
        in response.data
    )


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
            is_admin=True,
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


def test_admin_can_create_active_user_with_manual_password_without_email(
    client, app, monkeypatch
):
    def unexpected_email(*args, **kwargs):
        raise AssertionError("Manual account creation must not send email")

    monkeypatch.setattr("app.routes.auth_routes.send_email", unexpected_email)

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={
                "creation_method": "password",
                "email": "manual-user@example.com",
                "display_name": "Manual User",
                "password": "a-strong-manual-password",
                "confirm_password": "a-strong-manual-password",
                "submit": "1",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"User created with a manual password." in response.data
    with app.app_context():
        user = User.query.filter_by(email="manual-user@example.com").one()
        assert user.display_name == "Manual User"
        assert user.active is True
        assert user.invitation_pending is False
        assert check_password_hash(user.password, "a-strong-manual-password")


def test_manual_user_creation_requires_a_strong_matching_password(client, app):
    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={
                "creation_method": "password",
                "email": "weak-manual@example.com",
                "password": "short",
                "confirm_password": "different",
                "submit": "1",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Password must be at least 12 characters." in response.data
    assert b"Passwords must match." in response.data
    with app.app_context():
        assert (
            User.query.filter_by(email="weak-manual@example.com").first()
            is None
        )


def test_delegated_user_manager_cannot_escalate_groups_or_manage_super_admin(
    client, app
):
    with app.app_context():
        users_manage = Permission.query.filter_by(code="users.manage").one()
        settings_manage = Permission.query.filter_by(
            code="settings.manage"
        ).one()
        manager_group = PermissionGroup(name="Delegated User Managers")
        manager_group.permissions = [users_manage]
        higher_group = PermissionGroup(name="Higher Access")
        higher_group.permissions = [settings_manage]
        manager = User(
            email="delegated-manager@example.com",
            password=generate_password_hash("manager-password"),
            active=True,
        )
        manager.permission_groups = [manager_group]
        target = User(
            email="delegated-target@example.com",
            password=generate_password_hash("target-password"),
            active=True,
        )
        db.session.add_all([manager_group, higher_group, manager, target])
        db.session.commit()
        target_id = target.id
        higher_group_id = higher_group.id
        super_admin_id = (
            User.query.filter_by(is_admin=True, active=True).first().id
        )

    with client:
        login(client, "delegated-manager@example.com", "manager-password")
        access_page = client.get(f"/controlpanel/users/{target_id}/access")
        assert access_page.status_code == 200
        assert b"Higher Access" not in access_page.data

        escalation = client.post(
            f"/controlpanel/users/{target_id}/access",
            data={"access-group_ids": [str(higher_group_id)]},
            follow_redirects=True,
        )
        assert escalation.status_code == 200

        create_escalation = client.post(
            "/controlpanel/users",
            data={
                "creation_method": "password",
                "email": "forbidden-created-user@example.com",
                "password": "strong-created-password",
                "confirm_password": "strong-created-password",
                "group_ids": [str(higher_group_id)],
                "submit": "1",
            },
            follow_redirects=True,
        )
        assert create_escalation.status_code == 200

        assert client.get(f"/user_profile/{super_admin_id}").status_code == 403
        assert (
            client.get(
                f"/controlpanel/users/{super_admin_id}/access"
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/user_profile/{super_admin_id}",
                data={
                    "new_password": "attacker-controlled-password",
                    "confirm_password": "attacker-controlled-password",
                },
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/controlpanel/users",
                data={"user_id": super_admin_id, "action": "toggle_active"},
            ).status_code
            == 403
        )
        assert (
            client.post(f"/delete_user/{super_admin_id}", data={}).status_code
            == 403
        )

        user_list = client.get("/controlpanel/users")
        assert user_list.status_code == 200
        assert b"Super admin required" in user_list.data
        assert (
            f'href="/controlpanel/users/{target_id}/access"'.encode()
            in user_list.data
        )
        assert (
            f'href="/controlpanel/users/{super_admin_id}/access"'.encode()
            not in user_list.data
        )

    with app.app_context():
        target = db.session.get(User, target_id)
        assert target.permission_groups == []
        assert (
            User.query.filter_by(
                email="forbidden-created-user@example.com"
            ).first()
            is None
        )
        super_admin = db.session.get(User, super_admin_id)
        assert check_password_hash(super_admin.password, "adminpass")


def test_user_list_hides_destructive_actions_for_current_last_super_admin(
    client, app
):
    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.get("/controlpanel/users")

    assert response.status_code == 200
    assert b"Current account is protected." in response.data
    assert b"Current account protected" in response.data
    assert b'value="toggle_active"' not in response.data
    assert b'value="toggle_super_admin"' not in response.data
    assert b"Are you sure you want to archive this user?" not in response.data


def test_server_preserves_current_last_active_super_admin(client, app):
    with app.app_context():
        admin = User.query.filter_by(email="admin@example.com").one()
        admin_id = admin.id

    with client:
        login(client, "admin@example.com", "adminpass")
        assert (
            client.post(
                "/controlpanel/users",
                data={"user_id": admin_id, "action": "toggle_active"},
                follow_redirects=True,
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/controlpanel/users",
                data={"user_id": admin_id, "action": "toggle_super_admin"},
                follow_redirects=True,
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/delete_user/{admin_id}",
                data={},
                follow_redirects=True,
            ).status_code
            == 200
        )

    with app.app_context():
        admin = db.session.get(User, admin_id)
        assert admin.active is True
        assert admin.is_super_admin is True


def test_last_active_super_admin_mutation_uses_transaction_row_lock(
    client, app, monkeypatch
):
    lock_calls = []
    with app.app_context():
        admin = User.query.filter_by(email="admin@example.com").one()
        admin_id = admin.id
        query_class = type(User.query)
        original_with_for_update = query_class.with_for_update

        def tracking_with_for_update(query, *args, **kwargs):
            lock_calls.append((args, kwargs))
            return original_with_for_update(query, *args, **kwargs)

        monkeypatch.setattr(
            query_class, "with_for_update", tracking_with_for_update
        )

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={"user_id": admin_id, "action": "toggle_super_admin"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert lock_calls
    with app.app_context():
        admin = db.session.get(User, admin_id)
        assert admin.active is True
        assert admin.is_super_admin is True


def test_delegated_manager_cannot_deactivate_or_archive_self(client, app):
    with app.app_context():
        users_manage = Permission.query.filter_by(code="users.manage").one()
        group = PermissionGroup(name="Self-protected User Managers")
        group.permissions = [users_manage]
        manager = User(
            email="self-protected-manager@example.com",
            password=generate_password_hash("manager-password"),
            active=True,
        )
        manager.permission_groups = [group]
        db.session.add_all([group, manager])
        db.session.commit()
        manager_id = manager.id

    with client:
        login(client, "self-protected-manager@example.com", "manager-password")
        assert (
            client.post(
                "/controlpanel/users",
                data={"user_id": manager_id, "action": "toggle_active"},
                follow_redirects=True,
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/delete_user/{manager_id}",
                data={},
                follow_redirects=True,
            ).status_code
            == 200
        )

    with app.app_context():
        assert db.session.get(User, manager_id).active is True


def test_pending_invite_direct_activation_endpoint_preserves_pending_state(
    client, app
):
    with app.app_context():
        pending_user = User(
            email="direct-activate-pending@example.com",
            password=generate_password_hash("temporary-password"),
            active=False,
            invitation_pending=True,
        )
        db.session.add(pending_user)
        db.session.commit()
        pending_user_id = pending_user.id

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            f"/activate_user/{pending_user_id}",
            data={},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Pending invites must be accepted" in response.data
    with app.app_context():
        pending_user = db.session.get(User, pending_user_id)
        assert pending_user.active is False
        assert pending_user.invitation_pending is True
