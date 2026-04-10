from __future__ import annotations

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
