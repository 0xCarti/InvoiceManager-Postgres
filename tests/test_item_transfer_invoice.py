from datetime import date, timedelta
import re

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    Event,
    EventLocation,
    EventStandSheetItem,
    Invoice,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    Transfer,
    TransferItem,
    TransferRequest,
    TransferRequestItem,
    User,
)
from app.services.dashboard_metrics import transfer_completion_by_location
from tests.permission_helpers import grant_item_workflow_permissions
from tests.utils import login


def create_user(app, email="user@example.com"):
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_item_workflow_permissions(user)
        return user.id


def test_item_lifecycle(client, app):
    create_user(app, "itemuser@example.com")

    with client:
        login(client, "itemuser@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "Widget",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        item = Item.query.filter_by(name="Widget").first()
        assert item is not None
        item_id = item.id

    with client:
        login(client, "itemuser@example.com", "pass")
        resp = client.post(
            f"/items/edit/{item_id}",
            data={
                "name": "Gadget",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        updated = db.session.get(Item, item_id)
        assert updated.name == "Gadget"

    with client:
        login(client, "itemuser@example.com", "pass")
        resp = client.post(f"/items/delete/{item_id}", follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Item, item_id).archived


def test_transfer_flow(client, app):
    user_id = create_user(app, "transfer@example.com")
    with app.app_context():
        loc1 = Location(name="A")
        loc2 = Location(name="B")
        item = Item(name="Thing", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.commit()
        loc1_id, loc2_id, item_id = loc1.id, loc2.id, item.id
        unit_id = unit.id

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 5,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        assert transfer is not None
        ti = TransferItem.query.filter_by(transfer_id=transfer.id).first()
        assert ti.item_id == item_id and ti.quantity == 5
        tid = transfer.id

    with client:
        login(client, "transfer@example.com", "pass")
        list_resp = client.get("/transfers")
        list_html = list_resp.get_data(as_text=True)
        assert 'id="transferConfirmationModal"' in list_html
        assert "transfer_confirmation_modal.js" in list_html
        assert "js-transfer-confirm-form" in list_html

        preflight = client.post(
            f"/transfers/complete/{tid}",
            headers={"X-Transfer-Confirmation-Check": "1"},
        )
        assert preflight.status_code == 200
        payload = preflight.get_json()
        assert payload["requires_confirmation"] is True
        assert "Confirm Transfer Completion" == payload["title"]
        assert any("negative inventory" in warning for warning in payload["warnings"])

    with app.app_context():
        assert not db.session.get(Transfer, tid).completed

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.get(f"/transfers/complete/{tid}")
        assert resp.status_code == 200
        assert b"Confirm Transfer Completion" in resp.data
        resp = client.post(
            f"/transfers/complete/{tid}",
            data={"_transfer_confirmed": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Transfer, tid).completed

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.get(
            f"/transfers/uncomplete/{tid}", follow_redirects=False
        )
        assert resp.status_code == 200
        assert b"Confirm Transfer Incomplete" in resp.data
        resp = client.post(
            f"/transfers/uncomplete/{tid}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        assert not db.session.get(Transfer, tid).completed

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.post(f"/transfers/delete/{tid}", follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Transfer, tid) is None


def test_public_transfer_request_converts_to_transfer(client, app):
    user_id = create_user(app, "transferrequest@example.com")
    with app.app_context():
        from_location = Location(name="Request Warehouse")
        to_location = Location(name="Request Canteen")
        item = Item(name="Request Stock", base_unit="each")
        db.session.add_all([from_location, to_location, item])
        db.session.flush()
        to_location.ensure_count_qr_token()
        unit = ItemUnit(
            item_id=item.id,
            name="case",
            factor=12,
            receiving_default=False,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.commit()
        from_location_id = from_location.id
        to_location_id = to_location.id
        token = to_location.count_qr_token
        item_id = item.id
        unit_id = unit.id

    response = client.get(f"/transfers/request/{token}")
    assert response.status_code == 200
    assert b"Request Canteen" in response.data

    response = client.post(
        f"/transfers/request/{token}",
        data={
            "requested_by_name": "Stand Staff",
            "notes": "Running low",
            "request-items-0-item": item_id,
            "request-items-0-unit": unit_id,
            "request-items-0-quantity": "2",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Transfer request submitted for manager review." in response.data

    with app.app_context():
        transfer_request = TransferRequest.query.one()
        request_id = transfer_request.id
        request_item = TransferRequestItem.query.one()
        assert transfer_request.to_location_id == to_location_id
        assert transfer_request.status == TransferRequest.STATUS_PENDING
        assert request_item.quantity == 24

    with client:
        login(client, "transferrequest@example.com", "pass")
        response = client.get("/transfers?record_type=requests")
        assert response.status_code == 200
        assert b"Request" in response.data
        assert b"Needs review" in response.data

        response = client.post(
            f"/transfers/requests/{request_id}",
            data={
                "action": "convert",
                "from_location_id": from_location_id,
                "to_location_id": to_location_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": "2",
                "review_note": "Approved",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Transfer Details" in response.data

    with app.app_context():
        transfer_request = db.session.get(TransferRequest, request_id)
        transfer = db.session.get(Transfer, transfer_request.converted_transfer_id)
        assert transfer_request.status == TransferRequest.STATUS_CONVERTED
        assert transfer is not None
        assert transfer.from_location_id == from_location_id
        assert transfer.to_location_id == to_location_id
        assert transfer.user_id == user_id
        assert transfer.transfer_items[0].quantity == 24


def test_transfer_forms_prefill_profile_default_from_location(client, app):
    user_id = create_user(app, "transferdefault@example.com")
    with app.app_context():
        default_from = Location(name="Default Transfer From")
        other_location = Location(name="Other Transfer Location")
        db.session.add_all([default_from, other_location])
        db.session.commit()
        default_from_id = default_from.id
        user = db.session.get(User, user_id)
        user.default_transfer_from_location_id = default_from_id
        db.session.commit()

    with client:
        login(client, "transferdefault@example.com", "pass")
        add_resp = client.get("/transfers/add")
        assert add_resp.status_code == 200
        add_page = add_resp.get_data(as_text=True)
        assert _selected_option_value(add_page, "from_location_id") == str(
            default_from_id
        )

        list_resp = client.get("/transfers")
        assert list_resp.status_code == 200
        list_page = list_resp.get_data(as_text=True)
        assert _selected_option_value(list_page, "add-from_location_id") == str(
            default_from_id
        )


def _selected_option_value(page, select_id):
    match = re.search(
        rf'<select[^>]+id="{re.escape(select_id)}"[^>]*>(?P<body>.*?)</select>',
        page,
        re.DOTALL,
    )
    assert match is not None
    selected = re.search(r"<option[^>]*\bselected\b[^>]*>", match.group("body"))
    assert selected is not None
    value = re.search(r'\bvalue="(?P<value>[^"]+)"', selected.group(0))
    assert value is not None
    return value.group("value")


def test_transfer_confirmation_preflight_without_warnings_does_not_complete(
    client, app
):
    user_id = create_user(app, "transfer-preflight@example.com")
    with app.app_context():
        loc1 = Location(name="Preflight From")
        loc2 = Location(name="Preflight To")
        item = Item(name="Preflight Thing", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.flush()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.add(
            LocationStandItem(
                location_id=loc1.id,
                item_id=item.id,
                expected_count=10,
            )
        )
        db.session.commit()
        loc1_id, loc2_id, item_id, unit_id = loc1.id, loc2.id, item.id, unit.id

    with client:
        login(client, "transfer-preflight@example.com", "pass")
        client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 5,
            },
            follow_redirects=True,
        )

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        assert transfer is not None
        transfer_id = transfer.id

    with client:
        login(client, "transfer-preflight@example.com", "pass")
        preflight = client.post(
            f"/transfers/complete/{transfer_id}",
            headers={"X-Transfer-Confirmation-Check": "1"},
        )

    assert preflight.status_code == 200
    assert preflight.get_json()["requires_confirmation"] is False
    with app.app_context():
        transfer = db.session.get(Transfer, transfer_id)
        assert transfer is not None
        assert not transfer.completed


def test_ajax_edit_transfer_updates_quantity(client, app):
    user_id = create_user(app, "ajaxedit@example.com")
    with app.app_context():
        loc1 = Location(name="AjaxFrom")
        loc2 = Location(name="AjaxTo")
        item = Item(name="AjaxThing", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.commit()
        loc1_id, loc2_id, item_id = loc1.id, loc2.id, item.id
        unit_id = unit.id

    transfer_id = None
    with client:
        login(client, "ajaxedit@example.com", "pass")
        add_resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )
        assert add_resp.status_code == 200

        with app.app_context():
            transfer = Transfer.query.filter_by(user_id=user_id).first()
            assert transfer is not None
            transfer_id = transfer.id

        edit_resp = client.post(
            f"/transfers/ajax_edit/{transfer_id}",
            data={
                "edit-from_location_id": loc1_id,
                "edit-to_location_id": loc2_id,
                "edit-items-0-item": item_id,
                "edit-items-0-unit": unit_id,
                "edit-items-0-quantity": 7,
            },
        )
        assert edit_resp.status_code == 200
        payload = edit_resp.get_json()
        assert payload["success"] is True

    with app.app_context():
        updated_transfer = db.session.get(Transfer, transfer_id)
        assert updated_transfer is not None
        assert len(updated_transfer.transfer_items) == 1
        assert updated_transfer.transfer_items[0].quantity == 7


def test_invoice_creation_total(client, app):
    create_user(app, "invoice@example.com")
    with app.app_context():
        customer = Customer(first_name="John", last_name="Doe")
        product = Product(name="Widget", price=10.0, cost=5.0)
        db.session.add_all([customer, product])
        db.session.commit()
        cust_id = customer.id

    with client:
        login(client, "invoice@example.com", "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": "Widget?2??"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Invoice created successfully" in resp.data

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        assert len(invoice.products) == 1
        assert invoice.products[0].quantity == 2
        assert round(invoice.total, 2) == 22.4


def test_transfer_expected_counts_updated(client, app):
    user_id = create_user(app, "expected@example.com")
    with app.app_context():
        loc1 = Location(name="L1")
        loc2 = Location(name="L2")
        item = Item(name="Countable", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.flush()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        today = date.today()
        event = Event(
            name="Transfer Event",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )
        db.session.add(event)
        db.session.flush()
        from_event_location = EventLocation(
            event_id=event.id,
            location_id=loc1.id,
        )
        to_event_location = EventLocation(
            event_id=event.id,
            location_id=loc2.id,
        )
        db.session.add_all([from_event_location, to_event_location])
        lsi1 = LocationStandItem(
            location_id=loc1.id, item_id=item.id, expected_count=0
        )
        lsi2 = LocationStandItem(
            location_id=loc2.id, item_id=item.id, expected_count=0
        )
        db.session.add_all([lsi1, lsi2])
        db.session.commit()
        loc1_id, loc2_id, item_id, unit_id = loc1.id, loc2.id, item.id, unit.id
        from_event_location_id = from_event_location.id
        to_event_location_id = to_event_location.id

    with client:
        login(client, "expected@example.com", "pass")
        resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 4,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        tid = transfer.id

    with client:
        login(client, "expected@example.com", "pass")
        resp = client.get(f"/transfers/complete/{tid}")
        assert resp.status_code == 200
        assert b"Confirm Transfer Completion" in resp.data
        resp = client.post(
            f"/transfers/complete/{tid}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        l1 = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        l2 = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        from_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=from_event_location_id,
            item_id=item_id,
        ).first()
        to_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=to_event_location_id,
            item_id=item_id,
        ).first()
        assert l1.expected_count == -4
        assert l2.expected_count == 4
        assert from_sheet is not None
        assert to_sheet is not None
        assert from_sheet.opening_count == 0
        assert to_sheet.opening_count == 0
        assert from_sheet.transferred_out == 4
        assert to_sheet.transferred_in == 4

    with client:
        login(client, "expected@example.com", "pass")
        resp = client.get(
            f"/transfers/uncomplete/{tid}", follow_redirects=True
        )
        assert resp.status_code == 200
        resp = client.post(
            f"/transfers/uncomplete/{tid}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        l1 = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        l2 = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        from_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=from_event_location_id,
            item_id=item_id,
        ).first()
        to_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=to_event_location_id,
            item_id=item_id,
        ).first()
        assert l1.expected_count == 0
        assert l2.expected_count == 0
        assert from_sheet.transferred_out == 0
        assert to_sheet.transferred_in == 0


def test_pre_event_transfer_updates_opening_counts_only(client, app):
    user_id = create_user(app, "futuretransfer@example.com")
    with app.app_context():
        loc1 = Location(name="Future From")
        loc2 = Location(name="Future To")
        item = Item(name="Future Countable", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.flush()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        today = date.today()
        event = Event(
            name="Future Transfer Event",
            start_date=today + timedelta(days=2),
            end_date=today + timedelta(days=4),
        )
        db.session.add(event)
        db.session.flush()
        from_event_location = EventLocation(
            event_id=event.id,
            location_id=loc1.id,
        )
        to_event_location = EventLocation(
            event_id=event.id,
            location_id=loc2.id,
        )
        db.session.add_all([from_event_location, to_event_location])
        lsi1 = LocationStandItem(
            location_id=loc1.id, item_id=item.id, expected_count=10
        )
        lsi2 = LocationStandItem(
            location_id=loc2.id, item_id=item.id, expected_count=0
        )
        db.session.add_all([lsi1, lsi2])
        db.session.commit()
        loc1_id, loc2_id, item_id, unit_id = loc1.id, loc2.id, item.id, unit.id
        from_event_location_id = from_event_location.id
        to_event_location_id = to_event_location.id

    with client:
        login(client, "futuretransfer@example.com", "pass")
        resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 4,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        tid = transfer.id

    with client:
        login(client, "futuretransfer@example.com", "pass")
        resp = client.get(f"/transfers/complete/{tid}")
        assert resp.status_code == 200
        resp = client.post(
            f"/transfers/complete/{tid}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        l1 = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        l2 = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        from_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=from_event_location_id,
            item_id=item_id,
        ).first()
        to_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=to_event_location_id,
            item_id=item_id,
        ).first()
        assert l1.expected_count == 6
        assert l2.expected_count == 4
        assert from_sheet is not None
        assert to_sheet is not None
        assert from_sheet.opening_count == 6
        assert to_sheet.opening_count == 4
        assert from_sheet.transferred_out == 0
        assert to_sheet.transferred_in == 0

    with client:
        login(client, "futuretransfer@example.com", "pass")
        resp = client.get(
            f"/transfers/uncomplete/{tid}", follow_redirects=True
        )
        assert resp.status_code == 200
        resp = client.post(
            f"/transfers/uncomplete/{tid}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        l1 = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        l2 = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        from_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=from_event_location_id,
            item_id=item_id,
        ).first()
        to_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=to_event_location_id,
            item_id=item_id,
        ).first()
        assert l1.expected_count == 10
        assert l2.expected_count == 0
        assert from_sheet.opening_count == 10
        assert to_sheet.opening_count == 0
        assert from_sheet.transferred_out == 0
        assert to_sheet.transferred_in == 0


def test_transfer_completion_by_location_only_counts_open_transfers(app):
    user_id = create_user(app, "transferdashboard@example.com")
    with app.app_context():
        from_location = Location(name="Dash From")
        to_location = Location(name="Dash To")
        item = Item(name="Dash Item", base_unit="each")
        db.session.add_all([from_location, to_location, item])
        db.session.commit()

        open_transfer = Transfer(
            from_location_id=from_location.id,
            to_location_id=to_location.id,
            user_id=user_id,
            from_location_name=from_location.name,
            to_location_name=to_location.name,
            completed=False,
        )
        open_transfer.transfer_items.append(
            TransferItem(
                item_id=item.id,
                quantity=10,
                completed_quantity=4,
                item_name=item.name,
            )
        )

        completed_transfer = Transfer(
            from_location_id=from_location.id,
            to_location_id=to_location.id,
            user_id=user_id,
            from_location_name=from_location.name,
            to_location_name=to_location.name,
            completed=True,
        )
        completed_transfer.transfer_items.append(
            TransferItem(
                item_id=item.id,
                quantity=5,
                completed_quantity=0,
                item_name=item.name,
            )
        )

        db.session.add_all([open_transfer, completed_transfer])
        db.session.commit()

        completion_rows = transfer_completion_by_location()

        assert len(completion_rows) == 1
        assert completion_rows[0]["location_name"] == "Dash To"
        assert completion_rows[0]["transfer_count"] == 1
        assert round(completion_rows[0]["completion_percent"], 1) == 40.0


def test_ajax_edit_stale_completed_transfer_reopens_and_reverses_counts(
    client, app
):
    user_id = create_user(app, "transferstale@example.com")
    with app.app_context():
        loc1 = Location(name="Stale From")
        loc2 = Location(name="Stale To")
        item = Item(name="Stale Item", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=loc1.id, item_id=item.id, expected_count=0
                ),
                LocationStandItem(
                    location_id=loc2.id, item_id=item.id, expected_count=0
                ),
            ]
        )
        db.session.commit()
        loc1_id, loc2_id, item_id, unit_id = loc1.id, loc2.id, item.id, unit.id

    with client:
        login(client, "transferstale@example.com", "pass")
        resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 4,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        assert transfer is not None
        transfer_id = transfer.id

    with client:
        login(client, "transferstale@example.com", "pass")
        confirm = client.get(f"/transfers/complete/{transfer_id}")
        assert confirm.status_code == 200
        resp = client.post(
            f"/transfers/complete/{transfer_id}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = db.session.get(Transfer, transfer_id)
        transfer_item = transfer.transfer_items[0]
        transfer_item.completed_quantity = 0
        transfer_item.completed_at = None
        transfer_item.completed_by_id = None
        transfer.completed = True
        db.session.commit()

        from_count = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        to_count = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        assert from_count.expected_count == -4
        assert to_count.expected_count == 4

    with client:
        login(client, "transferstale@example.com", "pass")
        edit_resp = client.post(
            f"/transfers/ajax_edit/{transfer_id}",
            data={
                "edit-from_location_id": loc1_id,
                "edit-to_location_id": loc2_id,
                "edit-items-0-item": item_id,
                "edit-items-0-unit": unit_id,
                "edit-items-0-quantity": 7,
            },
        )
        assert edit_resp.status_code == 200
        payload = edit_resp.get_json()
        assert payload["success"] is True
        assert payload["reopened"] is True

    with app.app_context():
        updated_transfer = db.session.get(Transfer, transfer_id)
        assert updated_transfer is not None
        assert not updated_transfer.completed
        assert len(updated_transfer.transfer_items) == 1
        assert updated_transfer.transfer_items[0].quantity == 7
        assert updated_transfer.transfer_items[0].completed_quantity == 0

        from_count = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        to_count = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        assert from_count.expected_count == 0
        assert to_count.expected_count == 0


def test_delete_partially_completed_transfer_reverses_expected_counts(
    client, app
):
    user_id = create_user(app, "transferpartial@example.com")
    with app.app_context():
        loc1 = Location(name="Partial From")
        loc2 = Location(name="Partial To")
        item1 = Item(name="Partial Item 1", base_unit="each")
        item2 = Item(name="Partial Item 2", base_unit="each")
        db.session.add_all([loc1, loc2, item1, item2])
        db.session.commit()
        unit1 = ItemUnit(
            item_id=item1.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        unit2 = ItemUnit(
            item_id=item2.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add_all([unit1, unit2])
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=loc1.id, item_id=item1.id, expected_count=0
                ),
                LocationStandItem(
                    location_id=loc2.id, item_id=item1.id, expected_count=0
                ),
                LocationStandItem(
                    location_id=loc1.id, item_id=item2.id, expected_count=0
                ),
                LocationStandItem(
                    location_id=loc2.id, item_id=item2.id, expected_count=0
                ),
            ]
        )
        db.session.commit()
        loc1_id = loc1.id
        loc2_id = loc2.id
        item1_id = item1.id
        item2_id = item2.id
        unit1_id = unit1.id
        unit2_id = unit2.id

    with client:
        login(client, "transferpartial@example.com", "pass")
        resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item1_id,
                "items-0-unit": unit1_id,
                "items-0-quantity": 4,
                "items-1-item": item2_id,
                "items-1-unit": unit2_id,
                "items-1-quantity": 6,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        assert transfer is not None
        transfer_id = transfer.id
        first_item = next(
            item for item in transfer.transfer_items if item.item_id == item1_id
        )
        first_item_id = first_item.id

    with client:
        login(client, "transferpartial@example.com", "pass")
        confirm = client.get(f"/transfers/items/complete/{first_item_id}")
        assert confirm.status_code == 200
        resp = client.post(
            f"/transfers/items/complete/{first_item_id}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = db.session.get(Transfer, transfer_id)
        assert transfer is not None
        assert not transfer.completed
        item1_from = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item1_id
        ).first()
        item1_to = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item1_id
        ).first()
        item2_from = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item2_id
        ).first()
        item2_to = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item2_id
        ).first()
        assert item1_from.expected_count == -4
        assert item1_to.expected_count == 4
        assert item2_from.expected_count == 0
        assert item2_to.expected_count == 0

    with client:
        login(client, "transferpartial@example.com", "pass")
        resp = client.post(
            f"/transfers/delete/{transfer_id}",
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Transfer, transfer_id) is None
        item1_from = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item1_id
        ).first()
        item1_to = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item1_id
        ).first()
        item2_from = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item2_id
        ).first()
        item2_to = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item2_id
        ).first()
        assert item1_from.expected_count == 0
        assert item1_to.expected_count == 0
        assert item2_from.expected_count == 0
        assert item2_to.expected_count == 0


def test_transfer_item_form_nested_csrf_disabled(app):
    with app.test_request_context('/'):
        from app.forms import TransferForm

        form = TransferForm(prefix='add')
        entry = form.items.entries[0]

        # Nested transfer item forms should not require CSRF tokens so that
        # dynamically added rows from JavaScript post successfully.
        assert not hasattr(entry, 'csrf_token')


def test_stand_sheet_shows_expected_counts(client, app):
    user_id = create_user(app, "stand@example.com")
    with app.app_context():
        loc1 = Location(name="SL1")
        loc2 = Location(name="SL2")
        item = Item(name="StandItem", base_unit="each")
        product = Product(name="StandProd", price=1.0, cost=0.5)
        db.session.add_all([loc1, loc2, item, product])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=loc1.id, item_id=item.id, expected_count=0
                ),
                LocationStandItem(
                    location_id=loc2.id, item_id=item.id, expected_count=0
                ),
            ]
        )
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=unit.id,
                quantity=1,
                countable=True,
            )
        )
        loc1.products.append(product)
        loc2.products.append(product)
        db.session.commit()
        loc1_id, loc2_id, item_id, unit_id = loc1.id, loc2.id, item.id, unit.id

    with client:
        login(client, "stand@example.com", "pass")
        client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 5,
            },
            follow_redirects=True,
        )

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        tid = transfer.id

    with client:
        login(client, "stand@example.com", "pass")
        resp_confirm = client.get(f"/transfers/complete/{tid}")
        assert b"Confirm Transfer Completion" in resp_confirm.data
        client.post(
            f"/transfers/complete/{tid}",
            data={"submit": "1"},
            follow_redirects=True,
        )
        resp1 = client.get(f"/locations/{loc1_id}/stand_sheet")
        resp2 = client.get(f"/locations/{loc2_id}/stand_sheet")
        assert b"-5" in resp1.data
        assert b"5" in resp2.data
