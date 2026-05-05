from __future__ import annotations

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Department,
    Event,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    PurchaseInvoice,
    PurchaseOrder,
    Transfer,
    User,
    UserDepartmentMembership,
    Vendor,
)
from app.routes import auth_routes
from app.services import notification_service
from tests.permission_helpers import grant_permissions
from tests.utils import login


def _capture_notifications(monkeypatch):
    sent = {"emails": [], "texts": []}

    def fake_send_email(to_address, subject, body):
        sent["emails"].append(
            {"to": to_address, "subject": subject, "body": body}
        )

    def fake_send_sms(to_number, body):
        sent["texts"].append({"to": to_number, "body": body})

    monkeypatch.setattr(notification_service, "send_email", fake_send_email)
    monkeypatch.setattr(notification_service, "send_sms", fake_send_sms)
    return sent


def _create_user(
    email: str,
    *,
    password: str = "pass",
    phone_number: str | None = None,
    **kwargs,
) -> int:
    user = User(
        email=email,
        password=generate_password_hash(password),
        active=True,
        phone_number=phone_number,
        **kwargs,
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def _create_transfer_setup():
    actor_id = _create_user("transfer-actor@example.com", password="pass")
    watcher_id = _create_user(
        "transfer-watcher@example.com",
        notify_transfers=True,
        notify_transfers_email=True,
        phone_number="2045551001",
    )
    from_location = Location(name="Warehouse")
    to_location = Location(name="Arena")
    item = Item(name="Cases", base_unit="each")
    unit = ItemUnit(
        item=item,
        name="each",
        factor=1,
        receiving_default=True,
        transfer_default=True,
    )
    db.session.add_all([from_location, to_location, item, unit])
    db.session.flush()
    db.session.add(
        LocationStandItem(
            location_id=from_location.id,
            item_id=item.id,
            expected_count=10,
        )
    )
    db.session.commit()
    return {
        "actor_id": actor_id,
        "watcher_id": watcher_id,
        "from_location_id": from_location.id,
        "to_location_id": to_location.id,
        "item_id": item.id,
    }


def _create_purchase_setup():
    actor_id = _create_user("purchase-actor@example.com", password="pass")
    watcher_id = _create_user(
        "purchase-watcher@example.com",
        notify_purchase_orders_email=True,
        notify_purchase_orders_text=True,
        phone_number="2045551002",
    )
    vendor = Vendor(first_name="Prairie", last_name="Foods")
    item = Item(name="Limes", base_unit="each")
    unit = ItemUnit(
        item=item,
        name="each",
        factor=1,
        receiving_default=True,
        transfer_default=True,
    )
    location = Location(name="Bar")
    db.session.add_all([vendor, item, unit, location])
    db.session.flush()
    db.session.add(
        LocationStandItem(
            location_id=location.id,
            item_id=item.id,
            expected_count=0,
        )
    )
    db.session.commit()
    return {
        "actor_id": actor_id,
        "watcher_id": watcher_id,
        "vendor_id": vendor.id,
        "item_id": item.id,
        "unit_id": unit.id,
        "location_id": location.id,
    }


def test_profile_saves_operational_notification_preferences(client, app):
    with app.app_context():
        user_id = _create_user("profile-operational@example.com", password="pass")

    transfer_created_email = notification_service.notification_preference_input_name(
        "transfer_created", "email"
    )
    transfer_completed_text = notification_service.notification_preference_input_name(
        "transfer_completed", "text"
    )
    purchase_received_email = notification_service.notification_preference_input_name(
        "purchase_order_received", "email"
    )
    event_locations_text = notification_service.notification_preference_input_name(
        "event_locations_assigned", "text"
    )
    message_email = notification_service.notification_preference_input_name(
        "message_received", "email"
    )
    bulletin_text = notification_service.notification_preference_input_name(
        "bulletin_posted", "text"
    )
    location_archived_email = notification_service.notification_preference_input_name(
        "location_archived", "email"
    )

    with client:
        login(client, "profile-operational@example.com", "pass")
        response = client.post(
            "/auth/profile",
            data={
                "phone_number": "2045551111",
                transfer_created_email: "y",
                transfer_completed_text: "y",
                purchase_received_email: "y",
                event_locations_text: "y",
                message_email: "y",
                bulletin_text: "y",
                location_archived_email: "y",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200

    with app.app_context():
        user = db.session.get(User, user_id)
        assert user is not None
        assert user.phone_number == "2045551111"
        assert user.notification_preferences["transfer_created"]["email"] is True
        assert user.notification_preferences["transfer_completed"]["text"] is True
        assert user.notification_preferences["purchase_order_received"]["email"] is True
        assert user.notification_preferences["event_locations_assigned"]["text"] is True
        assert user.notification_preferences["message_received"]["email"] is True
        assert user.notification_preferences["bulletin_posted"]["text"] is True
        assert user.notification_preferences["location_archived"]["email"] is True
        assert user.notification_preferences["transfer_created"]["text"] is False
        assert user.notify_transfers_email is False
        assert user.notify_transfers is False


def test_transfer_notifications_cover_create_and_complete(client, app, monkeypatch):
    sent = _capture_notifications(monkeypatch)
    with app.app_context():
        setup = _create_transfer_setup()
        actor = db.session.get(User, setup["actor_id"])
        actor_email = actor.email

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            "/transfers/add",
            data={
                "from_location_id": setup["from_location_id"],
                "to_location_id": setup["to_location_id"],
                "items-0-item": setup["item_id"],
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        transfer = Transfer.query.order_by(Transfer.id.desc()).first()
        assert transfer is not None
        transfer_id = transfer.id

    assert any("Transfer created" in email["subject"] for email in sent["emails"])
    assert any("Transfer created" in text["body"] for text in sent["texts"])

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            f"/transfers/complete/{transfer_id}",
            data={"submit": "y"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert any("Transfer completed" in email["subject"] for email in sent["emails"])
    assert any("Transfer completed" in text["body"] for text in sent["texts"])


def test_transfer_notifications_can_be_scoped_to_created_only(
    client, app, monkeypatch
):
    sent = _capture_notifications(monkeypatch)
    with app.app_context():
        setup = _create_transfer_setup()
        watcher = db.session.get(User, setup["watcher_id"])
        actor = db.session.get(User, setup["actor_id"])
        assert watcher is not None
        assert actor is not None
        watcher.notify_transfers = False
        watcher.notify_transfers_email = False
        watcher.notification_preferences = {
            "transfer_created": {"email": True, "text": True},
            "transfer_completed": {"email": False, "text": False},
            "transfer_reopened": {"email": False, "text": False},
            "transfer_updated": {"email": False, "text": False},
        }
        db.session.commit()
        actor_email = actor.email

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            "/transfers/add",
            data={
                "from_location_id": setup["from_location_id"],
                "to_location_id": setup["to_location_id"],
                "items-0-item": setup["item_id"],
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        transfer = Transfer.query.order_by(Transfer.id.desc()).first()
        assert transfer is not None
        transfer_id = transfer.id

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            f"/transfers/complete/{transfer_id}",
            data={"submit": "y"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert any("Transfer created" in email["subject"] for email in sent["emails"])
    assert any("Transfer created" in text["body"] for text in sent["texts"])
    assert not any("Transfer completed" in email["subject"] for email in sent["emails"])
    assert not any("Transfer completed" in text["body"] for text in sent["texts"])


def test_purchase_order_notifications_cover_lifecycle(
    client, app, monkeypatch
):
    sent = _capture_notifications(monkeypatch)
    with app.app_context():
        setup = _create_purchase_setup()
        actor = db.session.get(User, setup["actor_id"])
        actor_email = actor.email

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            "/purchase_orders/create",
            data={
                "vendor": setup["vendor_id"],
                "order_date": "2026-05-01",
                "expected_date": "2026-05-02",
                "delivery_charge": 1.25,
                "items-0-item": setup["item_id"],
                "items-0-unit": setup["unit_id"],
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        po = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first()
        assert po is not None
        po_id = po.id

    assert any(
        "Purchase order created" in email["subject"] for email in sent["emails"]
    )

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            f"/purchase_orders/{po_id}/mark_ordered",
            data={"next": "/purchase_orders"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert any(
        "Purchase order marked as ordered" in email["subject"]
        for email in sent["emails"]
    )

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2026-05-03",
                "gst": 0.25,
                "pst": 0.35,
                "delivery_charge": 1.25,
                "location_id": setup["location_id"],
                "items-0-item": setup["item_id"],
                "items-0-unit": setup["unit_id"],
                "items-0-quantity": 3,
                "items-0-cost": 2.5,
                "items-0-vendor_sku": "LIME-001",
                "items-0-location_id": 0,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert any(
        "Purchase order received" in email["subject"] for email in sent["emails"]
    )

    with app.app_context():
        invoice = PurchaseInvoice.query.order_by(PurchaseInvoice.id.desc()).first()
        assert invoice is not None
        invoice_id = invoice.id

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            f"/purchase_invoices/{invoice_id}/reverse",
            data={"submit": "y"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert any(
        "Purchase order reversed" in email["subject"] for email in sent["emails"]
    )


def test_event_and_location_notifications_cover_changes(
    client, app, monkeypatch
):
    sent = _capture_notifications(monkeypatch)
    with app.app_context():
        actor_id = _create_user("ops-actor@example.com", password="pass")
        _create_user(
            "ops-watcher@example.com",
            notify_events_email=True,
            notify_events_text=True,
            notify_locations_email=True,
            notify_locations_text=True,
            phone_number="2045551003",
        )
        actor = db.session.get(User, actor_id)
        actor_email = actor.email

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            "/locations/add",
            data={"name": "North Stand", "menu_id": 0, "default_playlist_id": 0},
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        location = Location.query.filter_by(name="North Stand").first()
        assert location is not None
        location_id = location.id

    assert any("Location created" in email["subject"] for email in sent["emails"])

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            f"/locations/edit/{location_id}",
            data={
                "name": "North Stand Updated",
                "menu_id": 0,
                "default_playlist_id": 0,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            "/events/create",
            data={
                "name": "Concert Load-In",
                "start_date": "2026-05-10",
                "end_date": "2026-05-10",
                "event_type": "other",
                "estimated_sales": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            "/events/create",
            data={
                "name": "Quick Closeout",
                "start_date": "2026-05-12",
                "end_date": "2026-05-12",
                "event_type": "other",
                "estimated_sales": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        event = Event.query.filter_by(name="Concert Load-In").first()
        assert event is not None
        event_id = event.id
        close_event = Event.query.filter_by(name="Quick Closeout").first()
        assert close_event is not None
        close_event_id = close_event.id

    assert any("Event created" in email["subject"] for email in sent["emails"])

    with client:
        login(client, actor_email, "pass")
        response = client.post(
            f"/events/{event_id}/edit",
            data={
                "name": "Concert Load-In Revised",
                "start_date": "2026-05-10",
                "end_date": "2026-05-11",
                "event_type": "other",
                "estimated_sales": "",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            f"/events/{event_id}/add_location",
            data={"location_id": [str(location_id)]},
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            f"/events/{close_event_id}/close",
            data={"csrf_token": ""},
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            f"/locations/delete/{location_id}",
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert any("Event updated" in email["subject"] for email in sent["emails"])
    assert any("Event closed" in email["subject"] for email in sent["emails"])
    assert any("Location archived" in email["subject"] for email in sent["emails"])


def test_user_admin_notifications_cover_invite_and_archive(
    client, app, monkeypatch
):
    sent = _capture_notifications(monkeypatch)
    monkeypatch.setattr(auth_routes, "send_email", lambda *args, **kwargs: None)

    with app.app_context():
        _create_user(
            "user-watch@example.com",
            notify_users_email=True,
            notify_users_text=True,
            phone_number="2045551004",
        )
        archived_user_id = _create_user("archive-me@example.com", password="pass")

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            "/controlpanel/users",
            data={
                "email": "invited-person@example.com",
                "display_name": "Invited Person",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            f"/delete_user/{archived_user_id}",
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert any("User invited" in email["subject"] for email in sent["emails"])
    assert any("User archived" in email["subject"] for email in sent["emails"])


def test_message_and_bulletin_notifications_reach_recipients(
    client, app, monkeypatch
):
    sent = _capture_notifications(monkeypatch)

    with app.app_context():
        department = Department(name="Communications", active=True)
        db.session.add(department)
        db.session.flush()

        manager_id = _create_user("manager-notify@example.com", password="pass")
        staff_id = _create_user(
            "staff-notify@example.com",
            notify_messages_email=True,
            notify_messages_text=True,
            notify_bulletins_email=True,
            notify_bulletins_text=True,
            phone_number="2045551005",
        )

        manager = db.session.get(User, manager_id)
        staff = db.session.get(User, staff_id)
        db.session.add_all(
            [
                UserDepartmentMembership(
                    user_id=manager.id,
                    department_id=department.id,
                    role="manager",
                    is_primary=True,
                ),
                UserDepartmentMembership(
                    user_id=staff.id,
                    department_id=department.id,
                    role="staff",
                    is_primary=True,
                ),
            ]
        )
        db.session.commit()
        grant_permissions(
            manager,
            "communications.view",
            "communications.send_direct",
            "communications.send_broadcast",
            "communications.manage_bulletin",
            group_name="Notification Communications Manager",
            description="Can send communications during notification tests.",
        )

    with client:
        login(client, "manager-notify@example.com", "pass")
        response = client.post(
            "/communications",
            data={
                "action": "send_message",
                "message-audience": "users",
                "message-recipient_user_ids": [str(staff_id)],
                "message-subject": "Inventory review",
                "message-body": "Please review the latest inventory counts.",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        response = client.post(
            "/communications",
            data={
                "action": "post_bulletin",
                "bulletin-audience": "users",
                "bulletin-recipient_user_ids": [str(staff_id)],
                "bulletin-subject": "Floor opening",
                "bulletin-body": "Front of house opens thirty minutes earlier today.",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    assert any("New message from" in email["subject"] for email in sent["emails"])
    assert any("New bulletin" in email["subject"] for email in sent["emails"])
