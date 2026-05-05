from datetime import datetime

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    TerminalSale,
    User,
)
from tests.permission_helpers import grant_event_permissions
from tests.utils import login


def _setup_terminal_sales_env(app):
    with app.app_context():
        user = User(
            email="event-multiday@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Multi Day Event Stand")
        item = Item(name="Multi Day Event Item", base_unit="each")
        product = Product(name="Multi Day Event Product", price=1.0, cost=0.5)
        db.session.add_all([user, location, item, product])
        db.session.commit()

        item_unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(item_unit)
        db.session.add(
            LocationStandItem(
                location_id=location.id, item_id=item.id, expected_count=10
            )
        )
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=item_unit.id,
                quantity=1,
                countable=True,
            )
        )
        location.products.append(product)
        db.session.commit()
        grant_event_permissions(user)

        return user.email, location.id, product.id


def _create_terminal_sales_event_location(client, app, *, event_name):
    email, location_id, product_id = _setup_terminal_sales_env(app)
    with client:
        login(client, email, "pass")
        client.post(
            "/events/create",
            data={
                "name": event_name,
                "start_date": "2026-05-01",
                "end_date": "2026-05-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        event = Event.query.filter_by(name=event_name).first()
        assert event is not None
        event_id = event.id

    with client:
        login(client, email, "pass")
        client.post(
            f"/events/{event_id}/add_location",
            data={"location_id": location_id},
            follow_redirects=True,
        )

    with app.app_context():
        event_location = EventLocation.query.filter_by(
            event_id=event_id, location_id=location_id
        ).first()
        assert event_location is not None
        return email, event_id, event_location.id, product_id


def _add_imported_sales(event_location_id, product_id):
    db.session.add_all(
        [
            TerminalSale(
                event_location_id=event_location_id,
                product_id=product_id,
                quantity=3.0,
                approval_batch_id="batch-day-1",
                sold_at=datetime(2026, 5, 1, 12, 0, 0),
            ),
            TerminalSale(
                event_location_id=event_location_id,
                product_id=product_id,
                quantity=5.0,
                approval_batch_id="batch-day-2",
                sold_at=datetime(2026, 5, 2, 12, 0, 0),
            ),
        ]
    )
    db.session.commit()


def test_terminal_sales_prefill_sums_imported_daily_rows(client, app):
    email, event_id, event_location_id, product_id = _create_terminal_sales_event_location(
        client, app, event_name="ImportedSalesPrefillEvent"
    )

    with app.app_context():
        _add_imported_sales(event_location_id, product_id)

    with client:
        login(client, email, "pass")
        response = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
        )

    assert response.status_code == 200
    assert b'value="8.0"' in response.data or b'value="8"' in response.data


def test_saving_terminal_sales_preserves_imported_daily_rows(client, app):
    email, event_id, event_location_id, product_id = _create_terminal_sales_event_location(
        client, app, event_name="ImportedSalesAdjustmentEvent"
    )

    with app.app_context():
        _add_imported_sales(event_location_id, product_id)

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/events/{event_id}/locations/{event_location_id}/sales/add",
            data={f"qty_{product_id}": 10},
            follow_redirects=True,
        )

    assert response.status_code == 200

    with app.app_context():
        sales = (
            TerminalSale.query.filter_by(
                event_location_id=event_location_id, product_id=product_id
            )
            .order_by(TerminalSale.sold_at.asc(), TerminalSale.id.asc())
            .all()
        )
        assert len(sales) == 3

        imported_sales = [sale for sale in sales if sale.approval_batch_id]
        manual_sales = [
            sale
            for sale in sales
            if sale.approval_batch_id is None and sale.pos_sales_import_id is None
        ]

        assert [sale.quantity for sale in imported_sales] == pytest.approx([3.0, 5.0])
        assert len(manual_sales) == 1
        assert manual_sales[0].quantity == pytest.approx(2.0)


def test_saving_terminal_sales_cannot_reduce_imported_total(client, app):
    email, event_id, event_location_id, product_id = _create_terminal_sales_event_location(
        client, app, event_name="ImportedSalesValidationEvent"
    )

    with app.app_context():
        _add_imported_sales(event_location_id, product_id)

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/events/{event_id}/locations/{event_location_id}/sales/add",
            data={f"qty_{product_id}": 6},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Lower this from the sales import review" in response.data

    with app.app_context():
        sales = (
            TerminalSale.query.filter_by(
                event_location_id=event_location_id, product_id=product_id
            )
            .order_by(TerminalSale.sold_at.asc(), TerminalSale.id.asc())
            .all()
        )
        assert len(sales) == 2
        assert [sale.quantity for sale in sales] == pytest.approx([3.0, 5.0])
