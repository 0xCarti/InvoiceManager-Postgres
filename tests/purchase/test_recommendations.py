import datetime
import re

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    Item,
    ItemUnit,
    Location,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    TerminalSale,
    Transfer,
    TransferItem,
    User,
    Vendor,
)
from app.utils.forecasting import DemandForecastingHelper
from tests.utils import login


def _seed_forecasting_data(app):
    with app.app_context():
        today = datetime.date.today()
        now = datetime.datetime.utcnow()

        user = User(
            email="forecast@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Future", last_name="Foods")
        location_main = Location(name="Main Stand")
        location_other = Location(name="Warehouse")
        item = Item(name="Widget", base_unit="each")
        unit = ItemUnit(
            item=item,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        product = Product(name="Widget Combo", price=10, cost=4)
        recipe = ProductRecipeItem(product=product, item=item, unit=unit, quantity=2)
        event = Event(name="Concert", start_date=today, end_date=today)
        event_location = EventLocation(event=event, location=location_main)

        db.session.add_all(
            [
                user,
                vendor,
                location_main,
                location_other,
                item,
                unit,
                product,
                recipe,
                event,
                event_location,
            ]
        )
        db.session.commit()

        sale = TerminalSale(
            event_location=event_location,
            product=product,
            quantity=5,
            sold_at=now - datetime.timedelta(days=1),
        )
        db.session.add(sale)
        db.session.commit()

        transfer_out = Transfer(
            from_location_id=location_main.id,
            to_location_id=location_other.id,
            user_id=user.id,
            from_location_name=location_main.name,
            to_location_name=location_other.name,
            date_created=now - datetime.timedelta(days=1),
            completed=True,
        )
        transfer_in = Transfer(
            from_location_id=location_other.id,
            to_location_id=location_main.id,
            user_id=user.id,
            from_location_name=location_other.name,
            to_location_name=location_main.name,
            date_created=now - datetime.timedelta(days=1),
            completed=True,
        )
        db.session.add_all([transfer_out, transfer_in])
        db.session.flush()

        db.session.add_all(
            [
                TransferItem(
                    transfer_id=transfer_out.id,
                    item_id=item.id,
                    quantity=2,
                    item_name=item.name,
                ),
                TransferItem(
                    transfer_id=transfer_in.id,
                    item_id=item.id,
                    quantity=1,
                    item_name=item.name,
                ),
            ]
        )

        po_closed = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=user.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
            order_date=today,
            expected_date=today,
            delivery_charge=0,
            received=True,
        )
        po_open = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=user.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
            order_date=today,
            expected_date=today,
            delivery_charge=0,
            received=False,
        )
        db.session.add_all([po_closed, po_open])
        db.session.flush()

        poi = PurchaseOrderItem(
            purchase_order_id=po_open.id,
            item_id=item.id,
            unit_id=unit.id,
            quantity=3,
        )
        db.session.add(poi)

        invoice = PurchaseInvoice(
            purchase_order_id=po_closed.id,
            user_id=user.id,
            location_id=location_main.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
            location_name=location_main.name,
            received_date=today,
            delivery_charge=0,
        )
        db.session.add(invoice)
        db.session.flush()

        invoice_item = PurchaseInvoiceItem(
            invoice_id=invoice.id,
            item_id=item.id,
            unit_id=unit.id,
            item_name=item.name,
            unit_name=unit.name,
            quantity=2,
            cost=5,
        )
        db.session.add(invoice_item)

        db.session.commit()

        return {
            "user_email": user.email,
            "vendor_id": vendor.id,
            "item_id": item.id,
            "unit_id": unit.id,
            "location_id": location_main.id,
            "recommended_key": f"{item.id}:{location_main.id}",
        }


def test_forecasting_helper_aggregates_sources(app):
    ctx = _seed_forecasting_data(app)
    with app.app_context():
        helper = DemandForecastingHelper(lookback_days=30, lead_time_days=2)
        results = helper.build_recommendations(
            location_ids=[ctx["location_id"]]
        )
        assert len(results) == 1
        rec = results[0]
        assert rec.history["sales_qty"] == pytest.approx(10)
        assert rec.history["transfer_out_qty"] == pytest.approx(2)
        assert rec.history["transfer_in_qty"] == pytest.approx(1)
        assert rec.history["invoice_qty"] == pytest.approx(2)
        assert rec.history["open_po_qty"] == pytest.approx(3)
        assert rec.base_consumption == pytest.approx(12)
        assert rec.adjusted_demand == pytest.approx(12)
        assert rec.recommended_quantity == pytest.approx(6)
        assert rec.default_unit_id == ctx["unit_id"]


def test_recommendations_route_json_and_seed(client, app):
    ctx = _seed_forecasting_data(app)
    order_date = datetime.date.today().isoformat()
    override_qty = 4

    with client:
        login(client, ctx["user_email"], "pass")

        json_resp = client.get("/purchase_orders/recommendations?format=json")
        assert json_resp.status_code == 200
        payload = json_resp.get_json()
        assert payload["data"]
        assert payload["data"][0]["recommended_quantity"] == pytest.approx(6)

        response = client.post(
            "/purchase_orders/recommendations",
            data={
                "action": "seed",
                "selected_lines": ctx["recommended_key"],
                f"override-{ctx['recommended_key']}": override_qty,
                "seed_vendor_id": ctx["vendor_id"],
                "seed_order_date": order_date,
                "seed_expected_date": order_date,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        item_match = re.search(
            r'name="items-0-item"[^>]*value="(\d+)"', html
        )
        assert item_match and int(item_match.group(1)) == ctx["item_id"]
        quantity_match = re.search(
            r'name="items-0-quantity"[^>]*value="([^"]+)"', html
        )
        assert quantity_match and float(quantity_match.group(1)) == pytest.approx(
            override_qty
        )

        create_resp = client.post(
            "/purchase_orders/create",
            data={
                "vendor": ctx["vendor_id"],
                "order_date": order_date,
                "expected_date": order_date,
                "delivery_charge": 0,
                "items-0-item": ctx["item_id"],
                "items-0-unit": ctx["unit_id"],
                "items-0-quantity": override_qty,
            },
            follow_redirects=True,
        )
        assert create_resp.status_code == 200

    with app.app_context():
        po = PurchaseOrder.query.order_by(PurchaseOrder.id.desc()).first()
        assert po is not None
        assert po.received is False
        assert po.items[0].quantity == pytest.approx(override_qty)
