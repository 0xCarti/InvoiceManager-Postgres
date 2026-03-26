import datetime
import re

from werkzeug.security import generate_password_hash

import pytest

from app import db
from app.models import (
    GLCode,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    PurchaseInvoice,
    PurchaseInvoiceDraft,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderItemArchive,
    Setting,
    User,
    Vendor,
)
from tests.utils import login


def extract_input_value(page: str, field_id: str) -> str:
    pattern = rf'id="{re.escape(field_id)}"[^>]*value="([^"]*)"'
    match = re.search(pattern, page)
    assert match is not None, f"Value for {field_id} not found"
    return match.group(1)


def extract_selected_option(page: str, select_id: str) -> str:
    select_pattern = rf'<select[^>]*id="{re.escape(select_id)}"[^>]*>(.*?)</select>'
    select_match = re.search(select_pattern, page, re.DOTALL)
    assert select_match is not None, f"Select {select_id} not found"
    options_html = select_match.group(1)
    option_match = re.search(
        r'(?:value="([^"]*)"[^>]*selected|selected[^>]*value="([^"]*)")',
        options_html,
    )
    assert option_match is not None, f"No selected option found for {select_id}"
    return option_match.group(1) or option_match.group(2)


def extract_selected_options(page: str, select_id: str):
    select_pattern = rf'<select[^>]*id="{re.escape(select_id)}"[^>]*>(.*?)</select>'
    select_match = re.search(select_pattern, page, re.DOTALL)
    assert select_match is not None, f"Select {select_id} not found"
    options_html = select_match.group(1)
    selected_values = []
    for match in re.finditer(
        r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>',
        options_html,
        re.DOTALL,
    ):
        option_html = match.group(0)
        if "selected" in option_html:
            selected_values.append(match.group(1))
    return selected_values


def setup_purchase(app):
    with app.app_context():
        user = User(
            email="buyer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Vend", last_name="Or")
        item = Item(name="Part", base_unit="each")
        unit = ItemUnit(
            item=item,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        location = Location(name="Main")
        db.session.add_all([user, vendor, item, unit, location])
        db.session.commit()
        lsi = LocationStandItem(
            location_id=location.id, item_id=item.id, expected_count=0
        )
        db.session.add(lsi)
        db.session.commit()
        return user.email, vendor.id, item.id, location.id, unit.id


def setup_purchase_with_case(app):
    """Setup purchase scenario with an additional case unit."""
    with app.app_context():
        user = User(
            email="casebuyer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Vend", last_name="Or")
        item = Item(name="CaseItem", base_unit="each")
        each_unit = ItemUnit(
            item=item,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        case_unit = ItemUnit(item=item, name="case", factor=24)
        location = Location(name="Main")
        db.session.add_all(
            [user, vendor, item, each_unit, case_unit, location]
        )
        db.session.commit()
        lsi = LocationStandItem(
            location_id=location.id, item_id=item.id, expected_count=0
        )
        db.session.add(lsi)
        db.session.commit()
        return user.email, vendor.id, item.id, location.id, case_unit.id


def test_purchase_and_receive(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-01-01",
                "expected_date": "2023-01-05",
                "delivery_charge": 2,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        po = PurchaseOrder.query.first()
        assert po is not None
        po_id = po.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-01-04",
                "gst": 0.25,
                "pst": 0.35,
                "delivery_charge": 2,
                "location_id": location_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
                "items-0-cost": 2.5,
                "items-0-location_id": 0,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 3
        assert item.cost == 2.5
        lsi = LocationStandItem.query.filter_by(
            location_id=location_id, item_id=item_id
        ).first()
        assert lsi.expected_count == 3
        assert (
            PurchaseOrderItemArchive.query.filter_by(
                purchase_order_id=po_id
            ).count()
            == 1
        )


def test_receive_form_includes_department_defaults(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with app.app_context():
        secondary = Location(name="Secondary")
        db.session.add(secondary)
        db.session.commit()
        default_location_id = secondary.id
        Setting.set_receive_location_defaults({"Kitchen": default_location_id})
        db.session.commit()

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-01-01",
                "expected_date": "2023-01-05",
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 1,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        resp = client.get(f"/purchase_orders/{po_id}/receive")
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert f'"Kitchen": {default_location_id}' in page


def test_purchase_order_item_filter(client, app):
    with app.app_context():
        password = generate_password_hash("pass")
        user = User(email="filter@example.com", password=password, active=True)
        vendor = Vendor(first_name="Alpha", last_name="Vendor")
        widget = Item(name="Widget", base_unit="each")
        gadget = Item(name="Gadget", base_unit="each")
        db.session.add_all([user, vendor, widget, gadget])
        db.session.commit()

        order1 = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=user.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
            order_date=datetime.date(2024, 1, 1),
            expected_date=datetime.date(2024, 1, 2),
            delivery_charge=0,
            received=False,
        )
        order2 = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=user.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
            order_date=datetime.date(2024, 1, 3),
            expected_date=datetime.date(2024, 1, 4),
            delivery_charge=0,
            received=False,
        )
        db.session.add_all([order1, order2])
        db.session.commit()

        item1 = PurchaseOrderItem(
            purchase_order_id=order1.id,
            item_id=widget.id,
            quantity=5,
            position=0,
        )
        item2 = PurchaseOrderItem(
            purchase_order_id=order2.id,
            item_id=gadget.id,
            quantity=3,
            position=0,
        )
        db.session.add_all([item1, item2])
        db.session.commit()

        order1_id = order1.id
        order2_id = order2.id
        vendor_id = vendor.id
        widget_id = widget.id
        gadget_id = gadget.id
        widget_name = widget.name
        gadget_name = gadget.name
        user_email = user.email

    with client:
        login(client, user_email, "pass")

        resp = client.get(f"/purchase_orders?item_id={widget_id}")
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert f'data-id="{order1_id}"' in page
        assert f'data-id="{order2_id}"' not in page
        assert f'<option value="{widget_id}" selected' in page
        assert f'<option value="{gadget_id}" selected' not in page
        assert "Items:" in page
        assert widget_name in page

        resp_multi = client.get(
            "/purchase_orders",
            query_string={
                "vendor_id": vendor_id,
                "item_id": [widget_id, gadget_id],
                "per_page": 1,
            },
        )
        assert resp_multi.status_code == 200
        page_multi = resp_multi.get_data(as_text=True)
        assert f'data-id="{order1_id}"' in page_multi
        assert f'data-id="{order2_id}"' in page_multi
        assert page_multi.count("Items:") >= 1
        assert widget_name in page_multi
        assert gadget_name in page_multi
        assert f'<option value="{widget_id}" selected' in page_multi
        assert f'<option value="{gadget_id}" selected' in page_multi
        assert f'item_id={widget_id}' in page_multi
        assert f'item_id={gadget_id}' in page_multi
        assert "Filtering by Vendor:" in page_multi


def test_item_cost_visible_on_items_page(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-01-01",
                "expected_date": "2023-01-05",
                "delivery_charge": 2,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-01-04",
                "gst": 0.25,
                "pst": 0.35,
                "delivery_charge": 2,
                "location_id": location_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
                "items-0-cost": 2.5,
                "items-0-location_id": 0,
            },
            follow_redirects=True,
        )

        resp = client.get("/items")
        assert f"{2.5:.6f} / each" in resp.get_data(as_text=True)


def test_purchase_order_multiple_items(client, app):
    with app.app_context():
        user = User(
            email="multi@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Multi", last_name="Vendor")
        item1 = Item(name="PartA", base_unit="each")
        item2 = Item(name="PartB", base_unit="each")
        loc = Location(name="Main")
        db.session.add_all([user, vendor, item1, item2, loc])
        db.session.commit()
        iu1 = ItemUnit(
            item_id=item1.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        iu2 = ItemUnit(
            item_id=item2.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add_all(
            [
                iu1,
                iu2,
                LocationStandItem(
                    location_id=loc.id, item_id=item1.id, expected_count=0
                ),
                LocationStandItem(
                    location_id=loc.id, item_id=item2.id, expected_count=0
                ),
            ]
        )
        db.session.commit()
        vendor_id = vendor.id
        item1_id = item1.id
        item2_id = item2.id
        unit1_id = iu1.id
        unit2_id = iu2.id

    with client:
        login(client, "multi@example.com", "pass")
        resp = client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-02-01",
                "expected_date": "2023-02-05",
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
        po = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first()
        assert po.vendor_id == vendor_id
        assert len(po.items) == 2
        ids = {i.item_id for i in po.items}
        assert ids == {item1_id, item2_id}


def test_receive_invoice_line_locations(client, app):
    with app.app_context():
        user = User(
            email="locations@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Local", last_name="Vendor")
        item1 = Item(name="Apples", base_unit="each")
        item2 = Item(name="Bananas", base_unit="each")
        unit1 = ItemUnit(
            item=item1,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        unit2 = ItemUnit(
            item=item2,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        loc_main = Location(name="Main")
        loc_secondary = Location(name="Secondary")
        db.session.add_all(
            [user, vendor, item1, item2, unit1, unit2, loc_main, loc_secondary]
        )
        db.session.commit()
        for itm in (item1, item2):
            for loc in (loc_main, loc_secondary):
                db.session.add(
                    LocationStandItem(
                        location_id=loc.id,
                        item_id=itm.id,
                        expected_count=0,
                    )
                )
        db.session.commit()

        vendor_id = vendor.id
        item1_id = item1.id
        item2_id = item2.id
        unit1_id = unit1.id
        unit2_id = unit2.id
        loc_main_id = loc_main.id
        loc_secondary_id = loc_secondary.id

    with client:
        login(client, "locations@example.com", "pass")
        resp = client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-03-01",
                "expected_date": "2023-03-05",
                "items-0-item": item1_id,
                "items-0-unit": unit1_id,
                "items-0-quantity": 5,
                "items-1-item": item2_id,
                "items-1-unit": unit2_id,
                "items-1-quantity": 7,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        po_id = PurchaseOrder.query.filter_by(vendor_id=vendor_id).first().id

    with client:
        login(client, "locations@example.com", "pass")
        resp = client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-03-06",
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "location_id": loc_main_id,
                "items-0-item": item1_id,
                "items-0-unit": unit1_id,
                "items-0-quantity": 5,
                "items-0-cost": 1.5,
                "items-0-location_id": 0,
                "items-1-item": item2_id,
                "items-1-unit": unit2_id,
                "items-1-quantity": 7,
                "items-1-cost": 2.0,
                "items-1-location_id": loc_secondary_id,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        invoice = PurchaseInvoice.query.order_by(PurchaseInvoice.id.desc()).first()
        assert invoice is not None
        line_locations = {item.position: item.location_id for item in invoice.items}
        assert line_locations[0] is None
        assert line_locations[1] == loc_secondary_id

        main_apples = LocationStandItem.query.filter_by(
            location_id=loc_main_id, item_id=item1_id
        ).first()
        assert main_apples.expected_count == 5

        secondary_bananas = LocationStandItem.query.filter_by(
            location_id=loc_secondary_id, item_id=item2_id
        ).first()
        assert secondary_bananas.expected_count == 7

        main_bananas = LocationStandItem.query.filter_by(
            location_id=loc_main_id, item_id=item2_id
        ).first()
        assert main_bananas.expected_count == 0

def test_receive_form_prefills_delivery_charge(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-03-01",
                "expected_date": "2023-03-05",
                "delivery_charge": 5.5,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po = PurchaseOrder.query.first()
        po_id = po.id
        assert po.delivery_charge == 5.5

    with client:
        login(client, email, "pass")
        resp = client.get(f"/purchase_orders/{po_id}/receive")
        assert resp.status_code == 200
        assert b'value="5.50"' in resp.data


def test_receive_prefills_items_and_return(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-04-01",
                "expected_date": "2023-04-05",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po = PurchaseOrder.query.first()
        po_id = po.id

    with client:
        login(client, email, "pass")
        resp = client.get(f"/purchase_orders/{po_id}/receive")
        assert resp.status_code == 200
        assert b'name="items-0-item"' in resp.data

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-04-06",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": -3,
                "items-0-cost": 1.5,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        inv_item = PurchaseInvoiceItem.query.first()
        assert inv_item.cost == 1.5
        assert inv_item.quantity == -3
        assert inv_item.unit_id == unit_id
        assert inv_item.line_total == -4.5
        invoice = PurchaseInvoice.query.first()
        assert invoice.total == -4.5


def test_receive_invoice_prefills_unit(client, app):
    """Receive form should retain unit selection from purchase order."""
    (
        email,
        vendor_id,
        item_id,
        location_id,
        case_unit_id,
    ) = setup_purchase_with_case(app)

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-04-01",
                "expected_date": "2023-04-05",
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        resp = client.get(f"/purchase_orders/{po_id}/receive")
        assert resp.status_code == 200
        assert f'data-selected="{case_unit_id}"' in resp.get_data(as_text=True)


def test_edit_purchase_order_prefills_unit(client, app):
    """Edit form should retain unit selection from original purchase order."""
    (
        email,
        vendor_id,
        item_id,
        location_id,
        case_unit_id,
    ) = setup_purchase_with_case(app)

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-01",
                "expected_date": "2023-07-05",
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        resp = client.get(f"/purchase_orders/edit/{po_id}")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert f'data-selected="{case_unit_id}"' in html

        resp = client.post(
            f"/purchase_orders/edit/{po_id}",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-01",
                "expected_date": "2023-07-06",
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 4,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        poi = PurchaseOrderItem.query.filter_by(
            purchase_order_id=po_id
        ).first()
        assert poi.unit_id == case_unit_id
        assert poi.quantity == 4
def test_edit_purchase_order_updates(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-05-01",
                "expected_date": "2023-05-05",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po = PurchaseOrder.query.first()
        po_id = po.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/purchase_orders/edit/{po_id}",
            data={
                "vendor": vendor_id,
                "order_date": "2023-05-01",
                "expected_date": "2023-05-06",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 5,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        poi = PurchaseOrderItem.query.filter_by(
            purchase_order_id=po_id
        ).first()
        assert poi.quantity == 5


def test_invoice_moves_and_reverse(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-06-01",
                "expected_date": "2023-06-05",
                "delivery_charge": 2,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po = PurchaseOrder.query.first()
        po_id = po.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-06-06",
                "location_id": location_id,
                "gst": 0.25,
                "pst": 0.35,
                "delivery_charge": 2,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
                "items-0-cost": 2.5,
            },
            follow_redirects=True,
        )

    with app.app_context():
        inv = PurchaseInvoice.query.first()
        assert round(inv.total, 2) == 10.10
        assert db.session.get(PurchaseOrder, po_id).received
        inv_id = inv.id

    with client:
        login(client, email, "pass")
        resp = client.get("/purchase_orders")
        assert f">{po_id}<".encode() not in resp.data
        resp = client.get("/purchase_invoices")
        assert str(inv_id).encode() in resp.data
        assert b"Main" in resp.data

    with client:
        login(client, email, "pass")
        client.get(
            f"/purchase_invoices/{inv_id}/reverse", follow_redirects=True
        )

    with app.app_context():
        assert PurchaseInvoice.query.get(inv_id) is None
        assert not db.session.get(PurchaseOrder, po_id).received
        item = db.session.get(Item, item_id)
        assert item.quantity == 0
        assert item.cost == 0
        lsi = LocationStandItem.query.filter_by(
            location_id=location_id, item_id=item_id
        ).first()
        assert lsi.expected_count == 0
        draft = PurchaseInvoiceDraft.query.filter_by(purchase_order_id=po_id).first()
        assert draft is not None


def test_reverse_invoice_prefills_form(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-06-01",
                "expected_date": "2023-06-05",
                "delivery_charge": 1.5,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po = PurchaseOrder.query.first()
        assert po is not None
        po_id = po.id

    initial_data = {
        "received_date": "2023-06-06",
        "location_id": location_id,
        "department": "Kitchen",
        "gst": 0.25,
        "pst": 0.35,
        "delivery_charge": 5.75,
        "invoice_number": "INV-123",
        "items-0-item": item_id,
        "items-0-unit": unit_id,
        "items-0-quantity": 3,
        "items-0-cost": 2.5,
        "items-0-location_id": 0,
        "items-0-gl_code": 0,
    }

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po_id}/receive",
            data=initial_data,
            follow_redirects=True,
        )

    with app.app_context():
        invoice = PurchaseInvoice.query.first()
        assert invoice is not None
        inv_id = invoice.id

    with client:
        login(client, email, "pass")
        client.get(
            f"/purchase_invoices/{inv_id}/reverse", follow_redirects=True
        )

    with app.app_context():
        draft = PurchaseInvoiceDraft.query.filter_by(purchase_order_id=po_id).first()
        assert draft is not None
        data = draft.data
        assert data["invoice_number"] == "INV-123"
        assert data["gst"] == pytest.approx(0.25)
        assert data["delivery_charge"] == pytest.approx(5.75)
        assert data["items"][0]["cost"] == pytest.approx(2.5)

    with client:
        login(client, email, "pass")
        resp = client.get(f"/purchase_orders/{po_id}/receive")
        page = resp.get_data(as_text=True)
        assert extract_input_value(page, "received_date") == "2023-06-06"
        assert extract_input_value(page, "invoice_number") == "INV-123"
        assert float(extract_input_value(page, "gst")) == pytest.approx(0.25)
        assert float(extract_input_value(page, "pst")) == pytest.approx(0.35)
        assert float(extract_input_value(page, "delivery_charge")) == pytest.approx(5.75)
        assert float(extract_input_value(page, "items-0-cost")) == pytest.approx(2.5)
        assert float(extract_input_value(page, "items-0-quantity")) == pytest.approx(3)
        assert extract_selected_option(page, "location_id") == str(location_id)
        assert extract_selected_option(page, "department") == "Kitchen"

        updated_data = initial_data.copy()
        updated_data["gst"] = 0.5
        client.post(
            f"/purchase_orders/{po_id}/receive",
            data=updated_data,
            follow_redirects=True,
        )

    with app.app_context():
        assert (PurchaseInvoiceDraft.query.filter_by(purchase_order_id=po_id).first() is None)
        invoice = PurchaseInvoice.query.order_by(PurchaseInvoice.id.desc()).first()
        assert invoice is not None
        assert invoice.gst == pytest.approx(0.5)
        assert invoice.pst == pytest.approx(0.35)
        assert invoice.delivery_charge == pytest.approx(5.75)
        assert invoice.invoice_number == "INV-123"

def test_receive_invoice_base_unit_cost(client, app):
    """Receiving items in cases should update item cost per base unit."""
    email, vendor_id, item_id, location_id, case_unit_id = (
        setup_purchase_with_case(app)
    )

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-01",
                "expected_date": "2023-07-05",
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 1,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        po = PurchaseOrder.query.first()
        po_id = po.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-07-06",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 1,
                "items-0-cost": 24,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 24
        assert item.cost == 1
        lsi = LocationStandItem.query.filter_by(
            location_id=location_id, item_id=item_id
        ).first()
        assert lsi.expected_count == 24


def test_case_item_cost_visible_on_items_page(client, app):
    """Cost for case-based items should be visible on the items list."""
    email, vendor_id, item_id, location_id, case_unit_id = (
        setup_purchase_with_case(app)
    )

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-01-01",
                "expected_date": "2023-01-05",
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 1,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-01-06",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 1,
                "items-0-cost": 24,
            },
            follow_redirects=True,
        )

        resp = client.get("/items")
        assert "1.000000 / each" in resp.get_data(as_text=True)


def test_item_cost_is_average(client, app):
    """Receiving multiple invoices averages the item cost."""
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-10",
                "expected_date": "2023-07-15",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        po1 = PurchaseOrder.query.first()
        po1_id = po1.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/purchase_orders/{po1_id}/receive",
            data={
                "received_date": "2023-07-16",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 2,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-20",
                "expected_date": "2023-07-25",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        po2 = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first()
        po2_id = po2.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/purchase_orders/{po2_id}/receive",
            data={
                "received_date": "2023-07-26",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 4,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 4
        assert item.cost == 3


def test_item_cost_average_uses_location_counts(client, app):
    """Weighted cost should use existing inventory even if item.quantity is stale."""
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)

    # Receive first invoice so location count updates
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-10",
                "expected_date": "2023-07-15",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po1_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po1_id}/receive",
            data={
                "received_date": "2023-07-16",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 2,
            },
            follow_redirects=True,
        )

    # Simulate stale global quantity
    with app.app_context():
        item = db.session.get(Item, item_id)
        item.quantity = 0
        db.session.commit()

    # Second invoice at higher cost should average with location inventory
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-20",
                "expected_date": "2023-07-25",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po2_id = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po2_id}/receive",
            data={
                "received_date": "2023-07-26",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 4,
            },
            follow_redirects=True,
        )

    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 4
        assert item.cost == 3


def test_item_cost_average_visible_on_items_page(client, app):
    """Items list should display weighted average cost after multiple invoices."""
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)

    # First purchase and receive at cost 2
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-10",
                "expected_date": "2023-07-15",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po1_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po1_id}/receive",
            data={
                "received_date": "2023-07-16",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 2,
            },
            follow_redirects=True,
        )

    # Second purchase and receive at cost 4
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-07-20",
                "expected_date": "2023-07-25",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po2_id = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po2_id}/receive",
            data={
                "received_date": "2023-07-26",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 4,
            },
            follow_redirects=True,
        )

        resp = client.get("/items")
        assert "3.000000 / each" in resp.get_data(as_text=True)


def test_weighted_cost_saved_with_case_unit(client, app):
    """Weighted average cost should persist when using a case unit."""
    email, vendor_id, item_id, location_id, case_unit_id = setup_purchase_with_case(app)

    # First purchase: 1 case at $24 per case -> cost per each = $1
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-09-01",
                "expected_date": "2023-09-05",
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 1,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po1_id = PurchaseOrder.query.first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po1_id}/receive",
            data={
                "received_date": "2023-09-06",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 1,
                "items-0-cost": 24,
            },
            follow_redirects=True,
        )

    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 24
        assert item.cost == pytest.approx(1)

    # Second purchase: 2 cases at $12 per case -> cost per each = $0.5
    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-09-10",
                "expected_date": "2023-09-15",
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po2_id = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first().id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po2_id}/receive",
            data={
                "received_date": "2023-09-16",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": case_unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 12,
            },
            follow_redirects=True,
        )

    with app.app_context():
        item = db.session.get(Item, item_id)
        # Quantity: 24 + 48 = 72 eaches
        assert item.quantity == 72
        # Weighted cost: (24*1 + 48*0.5) / 72 = 2/3
        assert item.cost == pytest.approx(2 / 3)


def test_reverse_invoice_restores_previous_cost(client, app):
    """Reversing an invoice restores the prior base unit cost."""
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-08-10",
                "expected_date": "2023-08-15",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po1 = PurchaseOrder.query.first()
        po1_id = po1.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po1_id}/receive",
            data={
                "received_date": "2023-08-16",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 2,
            },
            follow_redirects=True,
        )

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-08-20",
                "expected_date": "2023-08-25",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po2 = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first()
        po2_id = po2.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po2_id}/receive",
            data={
                "received_date": "2023-08-26",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 4,
            },
            follow_redirects=True,
        )

    with app.app_context():
        invoice = PurchaseInvoice.query.order_by(
            PurchaseInvoice.id.desc()
        ).first()
        inv_id = invoice.id

    with client:
        login(client, email, "pass")
        client.get(
            f"/purchase_invoices/{inv_id}/reverse", follow_redirects=True
        )

    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item.quantity == 2
        assert item.cost == 2


def test_delete_unreceived_purchase_order(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-08-01",
                "expected_date": "2023-08-05",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po = PurchaseOrder.query.first()
        po_id = po.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/purchase_orders/{po_id}/delete", follow_redirects=True
        )
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(PurchaseOrder, po_id) is None
        assert (
            PurchaseOrderItem.query.filter_by(purchase_order_id=po_id).count()
            == 0
        )


def test_invoice_retains_item_and_unit_names_after_unit_removed(client, app):
    email, vendor_id, item_id, location_id, unit_id = setup_purchase(app)

    with client:
        login(client, email, "pass")
        client.post(
            "/purchase_orders/create",
            data={
                "vendor": vendor_id,
                "order_date": "2023-09-01",
                "expected_date": "2023-09-05",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
            },
            follow_redirects=True,
        )

    with app.app_context():
        po = PurchaseOrder.query.first()
        po_id = po.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/purchase_orders/{po_id}/receive",
            data={
                "received_date": "2023-09-06",
                "location_id": location_id,
                "gst": 0,
                "pst": 0,
                "delivery_charge": 0,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 2,
                "items-0-cost": 1.5,
            },
            follow_redirects=True,
        )

    with app.app_context():
        invoice = PurchaseInvoice.query.first()
        inv_id = invoice.id

    # Remove the unit after the invoice is recorded
    with app.app_context():
        db.session.delete(db.session.get(ItemUnit, unit_id))
        db.session.commit()

    with app.app_context():
        inv_item = PurchaseInvoiceItem.query.filter_by(
            invoice_id=inv_id
        ).first()
        assert inv_item.item is not None
        assert inv_item.unit is None
        assert inv_item.item_name == "Part"
        assert inv_item.unit_name == "each"


def test_view_purchase_invoices_item_filters(client, app):
    with app.app_context():
        user = User(
            email="filter@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Filter", last_name="Vendor")
        location = Location(name="Filter Warehouse")
        item_alpha = Item(name="Item Alpha", base_unit="each")
        item_beta = Item(name="Item Beta", base_unit="each")
        item_gamma = Item(name="Item Gamma", base_unit="each")
        db.session.add_all(
            [user, vendor, location, item_alpha, item_beta, item_gamma]
        )
        db.session.commit()

        invoice_definitions = [
            ("INV-A", [item_alpha]),
            ("INV-B", [item_beta]),
            ("INV-C", [item_gamma]),
        ]
        for index, (invoice_number, invoice_items) in enumerate(
            invoice_definitions, start=1
        ):
            po = PurchaseOrder(
                vendor_id=vendor.id,
                user_id=user.id,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                order_date=datetime.date(2024, 1, index),
                expected_date=datetime.date(2024, 1, index + 1),
                delivery_charge=0,
                received=True,
            )
            db.session.add(po)
            db.session.flush()
            invoice = PurchaseInvoice(
                purchase_order_id=po.id,
                user_id=user.id,
                location_id=location.id,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                location_name=location.name,
                received_date=datetime.date(2024, 2, index),
                invoice_number=invoice_number,
            )
            db.session.add(invoice)
            db.session.flush()
            for position, item in enumerate(invoice_items):
                db.session.add(
                    PurchaseInvoiceItem(
                        invoice=invoice,
                        position=position,
                        item_id=item.id,
                        item_name=item.name,
                        quantity=1,
                        cost=10 + index,
                    )
                )
        db.session.commit()

        user_email = user.email
        alpha_id = item_alpha.id
        beta_id = item_beta.id
        gamma_id = item_gamma.id
        alpha_name = item_alpha.name
        gamma_name = item_gamma.name

    with client:
        login(client, user_email, "pass")

        resp = client.get(
            "/purchase_invoices",
            query_string=[("item_id", str(alpha_id))],
        )
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert "INV-A" in page
        assert "INV-B" not in page
        assert "INV-C" not in page
        selected = extract_selected_options(page, "item_id")
        assert selected == [str(alpha_id)]

        resp = client.get(
            "/purchase_invoices",
            query_string=[
                ("item_id", str(alpha_id)),
                ("item_id", str(gamma_id)),
            ],
        )
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert "INV-A" in page
        assert "INV-B" not in page
        assert "INV-C" in page
        selected = extract_selected_options(page, "item_id")
        assert set(selected) == {str(alpha_id), str(gamma_id)}
        assert "Filtering by Items:" in page
        assert alpha_name in page
        assert gamma_name in page

        resp = client.get(
            "/purchase_invoices",
            query_string=[
                ("item_id", "not-a-number"),
                ("item_id", str(beta_id)),
            ],
        )
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert "INV-A" not in page
        assert "INV-B" in page
        assert "INV-C" not in page
        selected = extract_selected_options(page, "item_id")
        assert selected == [str(beta_id)]

def test_view_purchase_invoices_amount_filters(client, app):
    with app.app_context():
        password = generate_password_hash("pass")
        user = User(email="filterbuyer@example.com", password=password, active=True)
        vendor = Vendor(first_name="Filter", last_name="Vendor")
        location = Location(name="Filter Location")
        item = Item(name="Filter Item", base_unit="each")
        db.session.add_all([user, vendor, location, item])
        db.session.commit()

        def make_invoice(
            invoice_number: str,
            quantity: float,
            cost: float,
            *,
            gst: float = 0.0,
            pst: float = 0.0,
            delivery: float = 0.0,
            received_date: datetime.date = datetime.date(2024, 1, 1),
        ) -> PurchaseInvoice:
            po = PurchaseOrder(
                vendor_id=vendor.id,
                user_id=user.id,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                order_date=received_date,
                expected_date=received_date,
                delivery_charge=0,
                received=True,
            )
            db.session.add(po)
            db.session.flush()

            invoice = PurchaseInvoice(
                purchase_order_id=po.id,
                user_id=user.id,
                location_id=location.id,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                location_name=location.name,
                received_date=received_date,
                invoice_number=invoice_number,
                gst=gst,
                pst=pst,
                delivery_charge=delivery,
            )
            db.session.add(invoice)
            db.session.flush()

            item_line = PurchaseInvoiceItem(
                invoice_id=invoice.id,
                position=0,
                item_id=item.id,
                item_name=item.name,
                unit_name="each",
                quantity=quantity,
                cost=cost,
            )
            db.session.add(item_line)
            return invoice

        invoice_low = make_invoice(
            "INV-LOW",
            2,
            3.0,
            gst=1.0,
            pst=0.0,
            delivery=1.0,
            received_date=datetime.date(2024, 1, 2),
        )
        invoice_mid = make_invoice(
            "INV-MID",
            2,
            5.0,
            gst=0.0,
            pst=0.0,
            delivery=0.0,
            received_date=datetime.date(2024, 1, 3),
        )
        invoice_high = make_invoice(
            "INV-HIGH",
            4,
            5.0,
            gst=2.0,
            pst=1.0,
            delivery=1.0,
            received_date=datetime.date(2024, 1, 4),
        )
        db.session.commit()

        totals = {
            invoice_low.invoice_number: 8.0,
            invoice_mid.invoice_number: 10.0,
            invoice_high.invoice_number: 24.0,
        }

    with client:
        login(client, "filterbuyer@example.com", "pass")

        resp_all = client.get("/purchase_invoices")
        assert resp_all.status_code == 200
        page_all = resp_all.get_data(as_text=True)
        for number in totals:
            assert number in page_all

        resp_gt = client.get(
            "/purchase_invoices",
            query_string={"amount_filter": "gt", "amount_value": 9},
        )
        assert resp_gt.status_code == 200
        page_gt = resp_gt.get_data(as_text=True)
        assert "INV-LOW" not in page_gt
        assert "INV-MID" in page_gt
        assert "INV-HIGH" in page_gt
        assert "Filtering by Amount: Greater than" in page_gt

        resp_lt = client.get(
            "/purchase_invoices",
            query_string={"amount_filter": "lt", "amount_value": 9},
        )
        assert resp_lt.status_code == 200
        page_lt = resp_lt.get_data(as_text=True)
        assert "INV-LOW" in page_lt
        assert "INV-MID" not in page_lt
        assert "INV-HIGH" not in page_lt

        resp_eq = client.get(
            "/purchase_invoices",
            query_string={"amount_filter": "eq", "amount_value": 10},
        )
        assert resp_eq.status_code == 200
        page_eq = resp_eq.get_data(as_text=True)
        assert "INV-LOW" not in page_eq
        assert "INV-MID" in page_eq
        assert "INV-HIGH" not in page_eq
        assert "Filtering by Amount: Equal to" in page_eq

        resp_bad_filter = client.get(
            "/purchase_invoices",
            query_string={"amount_filter": "between", "amount_value": 9},
        )
        assert resp_bad_filter.status_code == 200
        page_bad_filter = resp_bad_filter.get_data(as_text=True)
        for number in totals:
            assert number in page_bad_filter
        assert "Filtering by Amount" not in page_bad_filter

        resp_bad_value = client.get(
            "/purchase_invoices",
            query_string={"amount_filter": "gt", "amount_value": "abc"},
        )
        assert resp_bad_value.status_code == 200
        page_bad_value = resp_bad_value.get_data(as_text=True)
        for number in totals:
            assert number in page_bad_value
        assert "Filtering by Amount" not in page_bad_value

