from __future__ import annotations

from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from app import db
from app.models import Communication, CommunicationRecipient, Department, User, UserDepartmentMembership
from tests.permission_helpers import grant_permissions
from tests.utils import login


def create_user(
    email: str,
    *,
    password: str = "pass",
    display_name: str | None = None,
) -> int:
    user = User(
        email=email,
        display_name=display_name,
        password=generate_password_hash(password),
        active=True,
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def create_department(name: str) -> int:
    department = Department(name=name, active=True)
    db.session.add(department)
    db.session.commit()
    return department.id


def add_membership(user_id: int, department_id: int, *, role: str) -> None:
    db.session.add(
        UserDepartmentMembership(
            user_id=user_id,
            department_id=department_id,
            role=role,
            is_primary=True,
        )
    )
    db.session.commit()


def test_manager_can_broadcast_to_all_scoped_users(client, app):
    with app.app_context():
        manager_id = create_user("comm-manager@example.com")
        staff_one_id = create_user("comm-staff-one@example.com")
        staff_two_id = create_user("comm-staff-two@example.com")
        department_id = create_department("Warehouse")
        add_membership(manager_id, department_id, role="manager")
        add_membership(staff_one_id, department_id, role="staff")
        add_membership(staff_two_id, department_id, role="staff")

        manager = db.session.get(User, manager_id)
        grant_permissions(
            manager,
            "communications.view",
            "communications.send_broadcast",
            group_name="Comms Broadcast Manager",
            description="Broadcast messaging test group.",
        )

    with client:
        login(client, "comm-manager@example.com", "pass")
        response = client.post(
            "/communications",
            data={
                "action": "send_message",
                "message-audience": "all",
                "message-subject": "Shift change",
                "message-body": "Please review the updated labor plan.",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Message sent to 2 user(s)." in response.data

    with app.app_context():
        message = Communication.query.filter_by(subject="Shift change").first()
        assert message is not None
        assert message.audience_type == Communication.AUDIENCE_ALL
        recipient_user_ids = {
            receipt.user_id for receipt in message.recipients
        }
        assert recipient_user_ids == {staff_one_id, staff_two_id}


def test_manager_department_bulletin_is_scoped_to_department(client, app):
    with app.app_context():
        manager_id = create_user("bulletin-manager@example.com")
        staff_one_id = create_user("bulletin-staff-one@example.com")
        staff_two_id = create_user("bulletin-staff-two@example.com")
        warehouse_id = create_department("Warehouse")
        kitchen_id = create_department("Kitchen")
        add_membership(manager_id, warehouse_id, role="manager")
        add_membership(staff_one_id, warehouse_id, role="staff")
        add_membership(staff_two_id, kitchen_id, role="staff")

        manager = db.session.get(User, manager_id)
        grant_permissions(
            manager,
            "communications.view",
            "communications.send_broadcast",
            "communications.manage_bulletin",
            group_name="Bulletin Manager",
            description="Bulletin management test group.",
        )

    with client:
        login(client, "bulletin-manager@example.com", "pass")
        response = client.post(
            "/communications",
            data={
                "action": "post_bulletin",
                "bulletin-audience": "department",
                "bulletin-department_id": str(warehouse_id),
                "bulletin-subject": "Warehouse memo",
                "bulletin-body": "Forklift checks happen before opening.",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Bulletin posted for 2 user(s)." in response.data

    with app.app_context():
        bulletin = Communication.query.filter_by(subject="Warehouse memo").first()
        assert bulletin is not None
        assert bulletin.kind == Communication.KIND_BULLETIN
        assert bulletin.department_id == warehouse_id
        recipient_user_ids = {
            receipt.user_id for receipt in bulletin.recipients
        }
        assert recipient_user_ids == {manager_id, staff_one_id}
        assert staff_two_id not in recipient_user_ids


def test_new_scoped_employee_sees_existing_all_scope_bulletin(client, app):
    with app.app_context():
        manager_id = create_user("dynamic-bulletin-manager@example.com")
        staff_one_id = create_user("dynamic-bulletin-staff-one@example.com")
        warehouse_id = create_department("Dynamic Warehouse")
        add_membership(manager_id, warehouse_id, role="manager")
        add_membership(staff_one_id, warehouse_id, role="staff")

        manager = db.session.get(User, manager_id)
        grant_permissions(
            manager,
            "communications.view",
            "communications.send_broadcast",
            "communications.manage_bulletin",
            group_name="Dynamic Bulletin Manager",
            description="Can post dynamic all-scope bulletins.",
        )

    with client:
        login(client, "dynamic-bulletin-manager@example.com", "pass")
        response = client.post(
            "/communications",
            data={
                "action": "post_bulletin",
                "bulletin-audience": "all",
                "bulletin-subject": "Warehouse handbook",
                "bulletin-body": "This bulletin should reach future scoped staff too.",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Bulletin posted for 2 user(s)." in response.data

    with app.app_context():
        staff_two_id = create_user("dynamic-bulletin-staff-two@example.com")
        add_membership(staff_two_id, warehouse_id, role="staff")
        staff_two = db.session.get(User, staff_two_id)
        grant_permissions(
            staff_two,
            "communications.view",
            group_name="Dynamic Bulletin Reader",
            description="Can view dynamic bulletins.",
        )

    with client:
        login(client, "dynamic-bulletin-staff-two@example.com", "pass")
        inbox_response = client.get("/communications", follow_redirects=True)

    assert inbox_response.status_code == 200
    assert b"Warehouse handbook" in inbox_response.data

    with app.app_context():
        receipt = (
            CommunicationRecipient.query.join(Communication)
            .filter(
                Communication.subject == "Warehouse handbook",
                CommunicationRecipient.user_id == staff_two_id,
            )
            .first()
        )
        assert receipt is not None


def test_selected_user_bulletin_does_not_expand_to_new_staff(client, app):
    with app.app_context():
        manager_id = create_user("explicit-bulletin-manager@example.com")
        staff_one_id = create_user("explicit-bulletin-staff-one@example.com")
        warehouse_id = create_department("Explicit Warehouse")
        add_membership(manager_id, warehouse_id, role="manager")
        add_membership(staff_one_id, warehouse_id, role="staff")

        manager = db.session.get(User, manager_id)
        grant_permissions(
            manager,
            "communications.view",
            "communications.send_broadcast",
            "communications.manage_bulletin",
            group_name="Explicit Bulletin Manager",
            description="Can post explicit-user bulletins.",
        )

    with client:
        login(client, "explicit-bulletin-manager@example.com", "pass")
        response = client.post(
            "/communications",
            data={
                "action": "post_bulletin",
                "bulletin-audience": "users",
                "bulletin-recipient_user_ids": [str(staff_one_id)],
                "bulletin-subject": "Named training memo",
                "bulletin-body": "Only explicitly selected users should keep this memo.",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Bulletin posted for 1 user(s)." in response.data

    with app.app_context():
        staff_two_id = create_user("explicit-bulletin-staff-two@example.com")
        add_membership(staff_two_id, warehouse_id, role="staff")
        staff_two = db.session.get(User, staff_two_id)
        grant_permissions(
            staff_two,
            "communications.view",
            group_name="Explicit Bulletin Reader",
            description="Can view communications.",
        )

    with client:
        login(client, "explicit-bulletin-staff-two@example.com", "pass")
        inbox_response = client.get("/communications", follow_redirects=True)

    assert inbox_response.status_code == 200
    assert b"Named training memo" not in inbox_response.data

    with app.app_context():
        receipt = (
            CommunicationRecipient.query.join(Communication)
            .filter(
                Communication.subject == "Named training memo",
                CommunicationRecipient.user_id == staff_two_id,
            )
            .first()
        )
        assert receipt is None


def test_staff_can_read_direct_message_and_mark_it_read(client, app):
    with app.app_context():
        manager_id = create_user("direct-manager@example.com")
        staff_id = create_user("direct-staff@example.com")
        department_id = create_department("Retail")
        add_membership(manager_id, department_id, role="manager")
        add_membership(staff_id, department_id, role="staff")

        manager = db.session.get(User, manager_id)
        staff = db.session.get(User, staff_id)
        grant_permissions(
            manager,
            "communications.view",
            "communications.send_direct",
            group_name="Direct Sender",
            description="Direct messaging test group.",
        )
        grant_permissions(
            staff,
            "communications.view",
            group_name="Direct Recipient",
            description="Inbox viewing test group.",
        )

    with client:
        login(client, "direct-manager@example.com", "pass")
        send_response = client.post(
            "/communications",
            data={
                "action": "send_message",
                "message-audience": "users",
                "message-recipient_user_ids": [str(staff_id)],
                "message-subject": "Check in",
                "message-body": "Please confirm tomorrow's availability.",
            },
            follow_redirects=True,
        )
        assert send_response.status_code == 200

    with app.app_context():
        receipt = (
            CommunicationRecipient.query.join(Communication)
            .filter(
                Communication.subject == "Check in",
                CommunicationRecipient.user_id == staff_id,
            )
            .first()
        )
        assert receipt is not None
        receipt_id = receipt.id
        assert receipt.read_at is None

    with client:
        login(client, "direct-staff@example.com", "pass")
        inbox_response = client.get("/communications")
        assert inbox_response.status_code == 200
        assert b"Check in" in inbox_response.data

        mark_read_response = client.post(
            "/communications",
            data={
                "action": "mark_read",
                "receipt_id": str(receipt_id),
            },
            follow_redirects=True,
        )
        assert mark_read_response.status_code == 200

    with app.app_context():
        receipt = db.session.get(CommunicationRecipient, receipt_id)
        assert receipt is not None
        assert receipt.read_at is not None


def test_manager_cannot_post_bulletin_to_unmanaged_department(client, app):
    with app.app_context():
        manager_id = create_user("scope-manager@example.com")
        warehouse_id = create_department("Warehouse")
        kitchen_id = create_department("Kitchen")
        add_membership(manager_id, warehouse_id, role="manager")

        manager = db.session.get(User, manager_id)
        grant_permissions(
            manager,
            "communications.view",
            "communications.send_broadcast",
            "communications.manage_bulletin",
            group_name="Scoped Bulletin Manager",
            description="Scoped bulletin test group.",
        )

    with client:
        login(client, "scope-manager@example.com", "pass")
        response = client.post(
            "/communications",
            data={
                "action": "post_bulletin",
                "bulletin-audience": "department",
                "bulletin-department_id": str(kitchen_id),
                "bulletin-subject": "Kitchen memo",
                "bulletin-body": "This should not be allowed.",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"outside your messaging scope" in response.data

    with app.app_context():
        assert Communication.query.filter_by(subject="Kitchen memo").count() == 0


def test_manager_with_history_permission_can_view_messages_between_other_users(
    client, app
):
    with app.app_context():
        manager_id = create_user("history-manager@example.com")
        staff_sender_id = create_user(
            "history-staff-one@example.com",
            display_name="Taylor Sender",
        )
        staff_recipient_id = create_user(
            "history-staff-two@example.com",
            display_name="Jordan Receiver",
        )
        department_id = create_department("Operations")
        add_membership(manager_id, department_id, role="manager")
        add_membership(staff_sender_id, department_id, role="staff")
        add_membership(staff_recipient_id, department_id, role="staff")

        manager = db.session.get(User, manager_id)
        sender = db.session.get(User, staff_sender_id)
        grant_permissions(
            manager,
            "communications.view_history",
            group_name="Communication History Viewer",
            description="Can review scoped message history.",
        )
        grant_permissions(
            sender,
            "communications.view",
            "communications.send_direct",
            group_name="Direct Message Sender",
            description="Can send direct messages.",
        )

    with client:
        login(client, "history-staff-one@example.com", "pass")
        send_response = client.post(
            "/communications",
            data={
                "action": "send_message",
                "message-audience": "users",
                "message-recipient_user_ids": [str(staff_recipient_id)],
                "message-subject": "Private handoff",
                "message-body": "Inventory counts are on the back table.",
            },
            follow_redirects=True,
        )
        assert send_response.status_code == 200

        login(client, "history-manager@example.com", "pass")
        history_response = client.get("/communications", follow_redirects=True)

    assert history_response.status_code == 200
    assert b"Scoped Message History" in history_response.data
    assert b"Private handoff" in history_response.data
    assert b"Inventory counts are on the back table." in history_response.data
    assert b"Taylor Sender" in history_response.data
    assert b"Jordan Receiver" in history_response.data


def test_bulletin_board_uses_paginated_list_detail_layout(client, app):
    with app.app_context():
        user_id = create_user("bulletin-reader@example.com")
        user = db.session.get(User, user_id)
        grant_permissions(
            user,
            "communications.view",
            "dashboard.view",
            group_name="Bulletin Reader",
            description="Can view communications and the dashboard.",
        )

        for index in range(11):
            ordinal = f"{index + 1:02d}"
            bulletin = Communication(
                kind=Communication.KIND_BULLETIN,
                sender=user,
                audience_type=Communication.AUDIENCE_USERS,
                subject=f"Bulletin {ordinal}",
                body=f"Full bulletin body {ordinal}",
                pinned=True,
                active=True,
                created_at=datetime.utcnow() - timedelta(minutes=index),
            )
            db.session.add(bulletin)
            bulletin.recipients = [CommunicationRecipient(user_id=user.id)]
        db.session.commit()
        oldest_bulletin_id = (
            Communication.query.filter_by(subject="Bulletin 11").one().id
        )

    with client:
        login(client, "bulletin-reader@example.com", "pass")
        first_page = client.get("/communications", follow_redirects=True)
        second_page = client.get("/communications?bulletin_page=2", follow_redirects=True)
        expanded_response = client.get(
            f"/communications?bulletin_page=2&bulletin_id={oldest_bulletin_id}",
            follow_redirects=True,
        )

    first_body = first_page.get_data(as_text=True)
    second_body = second_page.get_data(as_text=True)
    expanded_body = expanded_response.get_data(as_text=True)

    assert first_page.status_code == 200
    assert "Page 1 of 2" in first_body
    assert "Bulletin 01" in first_body
    assert "Bulletin 11" not in first_body
    assert "Click a bulletin header to open the full memo." in first_body
    assert "Pin" in first_body
    assert "Full bulletin body 01" not in first_body

    assert second_page.status_code == 200
    assert "Page 2 of 2" in second_body
    assert "Bulletin 11" in second_body
    assert "Full bulletin body 11" not in second_body

    assert expanded_response.status_code == 200
    assert f"bulletin_id={oldest_bulletin_id}" in (
        expanded_response.request.path
        + "?"
        + expanded_response.request.query_string.decode()
    )
    assert "Full bulletin body 11" in expanded_body
    assert "Archive Bulletin" in expanded_body


def test_user_can_save_bulletin_for_dashboard_from_communications(client, app):
    with app.app_context():
        user_id = create_user("bulletin-dashboard@example.com")
        user = db.session.get(User, user_id)
        grant_permissions(
            user,
            "communications.view",
            "dashboard.view",
            group_name="Bulletin Dashboard",
            description="Can save bulletins for the dashboard.",
        )
        bulletin = Communication(
            kind=Communication.KIND_BULLETIN,
            sender=user,
            audience_type=Communication.AUDIENCE_USERS,
            subject="Training bulletin",
            body="This bulletin should be saved to the dashboard.",
            pinned=True,
            active=True,
        )
        db.session.add(bulletin)
        bulletin.recipients = [CommunicationRecipient(user_id=user.id)]
        db.session.commit()
        bulletin_id = bulletin.id

    with client:
        login(client, "bulletin-dashboard@example.com", "pass")
        save_response = client.post(
            "/communications",
            data={
                "action": "toggle_dashboard_bulletin",
                "communication_id": str(bulletin_id),
                "save_on_dashboard": "1",
            },
            follow_redirects=True,
        )
        dashboard_response = client.get("/", follow_redirects=True)

    save_body = save_response.get_data(as_text=True)
    dashboard_body = dashboard_response.get_data(as_text=True)

    assert save_response.status_code == 200
    assert "Bulletin saved to your dashboard." in save_body
    assert "Unpin" in save_body

    assert dashboard_response.status_code == 200
    assert "Training bulletin" in dashboard_body
    assert "Saved" in dashboard_body


def test_bulletin_read_receipts_require_permission(client, app):
    with app.app_context():
        manager_id = create_user("bulletin-receipts-manager@example.com")
        elevated_manager_id = create_user("bulletin-receipts-manager-elevated@example.com")
        coworker_id = create_user("bulletin-receipts-staff@example.com")
        manager = db.session.get(User, manager_id)
        elevated_manager = db.session.get(User, elevated_manager_id)
        grant_permissions(
            manager,
            "communications.view",
            group_name="Bulletin Receipt Viewer",
            description="Can view the communications page.",
        )
        grant_permissions(
            elevated_manager,
            "communications.view",
            "communications.view_bulletin_receipts",
            group_name="Bulletin Receipt Viewer Elevated",
            description="Can view bulletin read receipts.",
        )
        bulletin = Communication(
            kind=Communication.KIND_BULLETIN,
            sender=manager,
            audience_type=Communication.AUDIENCE_USERS,
            subject="Receipt audit bulletin",
            body="Visibility should only appear with the right permission.",
            pinned=True,
            active=True,
        )
        db.session.add(bulletin)
        bulletin.recipients = [
            CommunicationRecipient(user_id=manager.id, read_at=datetime.utcnow()),
            CommunicationRecipient(user_id=elevated_manager.id, read_at=datetime.utcnow()),
            CommunicationRecipient(user_id=coworker_id, read_at=datetime.utcnow()),
        ]
        db.session.commit()
        bulletin_id = bulletin.id

    with client:
        login(client, "bulletin-receipts-manager@example.com", "pass")
        hidden_response = client.get(
            f"/communications?bulletin_id={bulletin_id}",
            follow_redirects=True,
        )

    hidden_body = hidden_response.get_data(as_text=True)
    assert hidden_response.status_code == 200
    assert "Read receipts" not in hidden_body

    with client:
        login(client, "bulletin-receipts-manager-elevated@example.com", "pass")
        visible_response = client.get(
            f"/communications?bulletin_id={bulletin_id}",
            follow_redirects=True,
        )

    visible_body = visible_response.get_data(as_text=True)
    assert visible_response.status_code == 200
    assert "Read receipts" in visible_body
    assert "bulletin-receipts-staff@example.com" in visible_body
