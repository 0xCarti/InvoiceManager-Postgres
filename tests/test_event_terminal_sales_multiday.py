from datetime import date, datetime

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationOperatingDay,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    ProductSellableAmount,
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
                "event_type": "other",
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


def test_terminal_sales_prefill_is_scoped_to_operating_day(client, app):
    email, event_id, event_location_id, product_id = _create_terminal_sales_event_location(
        client, app, event_name="ImportedSalesPrefillEvent"
    )

    with app.app_context():
        _add_imported_sales(event_location_id, product_id)
        day_two_product = Product(
            name="Day Two Only Product",
            price=4.0,
            cost=1.0,
        )
        db.session.add(day_two_product)
        db.session.flush()
        db.session.add(
            TerminalSale(
                event_location_id=event_location_id,
                product_id=day_two_product.id,
                quantity=1.0,
                sold_at=datetime(2026, 5, 2, 14, 0, 0),
            )
        )
        db.session.commit()

    with client:
        login(client, email, "pass")
        day_one_response = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-01"
        )
        day_two_response = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-02"
        )

    assert day_one_response.status_code == 200
    assert day_two_response.status_code == 200
    assert b"Friday, May 01, 2026" in day_one_response.data
    assert (
        b'value="3.0"' in day_one_response.data
        or b'value="3"' in day_one_response.data
    )
    assert b'value="5.0"' not in day_one_response.data
    assert b"Day Two Only Product" not in day_one_response.data
    assert b"Saturday, May 02, 2026" in day_two_response.data
    assert (
        b'value="5.0"' in day_two_response.data
        or b'value="5"' in day_two_response.data
    )
    assert b'value="3.0"' not in day_two_response.data
    assert b"Day Two Only Product" in day_two_response.data


def test_view_event_sales_links_are_date_specific(client, app):
    email, event_id, event_location_id, _ = _create_terminal_sales_event_location(
        client, app, event_name="DailySalesLinksEvent"
    )

    with client:
        login(client, email, "pass")
        response = client.get(f"/events/{event_id}")
        body = response.data.decode()

    assert response.status_code == 200
    base_path = f"/events/{event_id}/locations/{event_location_id}/sales/add"
    assert f'{base_path}?operating_date=2026-05-01' in body
    assert f'{base_path}?operating_date=2026-05-02' in body
    assert f'href="{base_path}"' not in body


def test_saving_terminal_sales_only_changes_selected_day(client, app):
    email, event_id, event_location_id, product_id = _create_terminal_sales_event_location(
        client, app, event_name="ImportedSalesAdjustmentEvent"
    )

    with app.app_context():
        _add_imported_sales(event_location_id, product_id)
        day_two_manual = TerminalSale(
            event_location_id=event_location_id,
            product_id=product_id,
            quantity=2.0,
            sold_at=datetime(2026, 5, 2, 13, 0, 0),
        )
        db.session.add(day_two_manual)
        db.session.commit()
        day_two_manual_id = day_two_manual.id

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-01",
            data={f"qty_{product_id}": 4},
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith(
        f"/events/{event_id}#event-day-pane-2026-05-01"
    )

    with app.app_context():
        sales = (
            TerminalSale.query.filter_by(
                event_location_id=event_location_id, product_id=product_id
            )
            .order_by(TerminalSale.sold_at.asc(), TerminalSale.id.asc())
            .all()
        )
        assert len(sales) == 4

        imported_sales = [sale for sale in sales if sale.approval_batch_id]
        manual_sales = [
            sale
            for sale in sales
            if sale.approval_batch_id is None and sale.pos_sales_import_id is None
        ]

        assert [sale.quantity for sale in imported_sales] == pytest.approx([3.0, 5.0])
        assert len(manual_sales) == 2
        day_one_manual = next(
            sale for sale in manual_sales if sale.sold_at.date() == date(2026, 5, 1)
        )
        unchanged_day_two_manual = db.session.get(TerminalSale, day_two_manual_id)
        assert day_one_manual.quantity == pytest.approx(1.0)
        assert day_one_manual.sold_at == datetime(2026, 5, 1, 12, 0, 0)
        assert unchanged_day_two_manual.quantity == pytest.approx(2.0)
        assert unchanged_day_two_manual.sold_at == datetime(2026, 5, 2, 13, 0, 0)


def test_saving_terminal_sales_cannot_reduce_imported_total(client, app):
    email, event_id, event_location_id, product_id = _create_terminal_sales_event_location(
        client, app, event_name="ImportedSalesValidationEvent"
    )

    with app.app_context():
        _add_imported_sales(event_location_id, product_id)

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-01",
            data={f"qty_{product_id}": 2},
            follow_redirects=True,
        )
        non_finite_response = client.post(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-01",
            data={f"qty_{product_id}": "NaN"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Lower this from the sales import review" in response.data
    assert non_finite_response.status_code == 200
    assert b"Enter a finite quantity" in non_finite_response.data

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


def test_negative_imported_total_does_not_create_a_manual_offset(client, app):
    email, event_id, event_location_id, product_id = (
        _create_terminal_sales_event_location(
            client, app, event_name="NegativeImportedDailySalesEvent"
        )
    )
    with app.app_context():
        imported_refund = TerminalSale(
            event_location_id=event_location_id,
            product_id=product_id,
            quantity=-2.0,
            approval_batch_id="negative-import",
            sold_at=datetime(2026, 5, 1, 12, 0, 0),
        )
        db.session.add(imported_refund)
        db.session.commit()
        imported_refund_id = imported_refund.id

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-01",
            data={f"qty_{product_id}": -2},
            follow_redirects=False,
        )

    assert response.status_code == 302
    with app.app_context():
        rows = TerminalSale.query.filter_by(
            event_location_id=event_location_id,
            product_id=product_id,
        ).all()
        assert [row.id for row in rows] == [imported_refund_id]
        assert rows[0].quantity == pytest.approx(-2.0)


def test_terminal_sales_requires_a_configured_operating_day(client, app):
    email, event_id, event_location_id, _ = _create_terminal_sales_event_location(
        client, app, event_name="SalesOperatingDateValidationEvent"
    )

    with client:
        login(client, email, "pass")
        missing = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
        )
        malformed = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=not-a-date"
        )
        outside = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-03"
        )

    assert missing.status_code == 404
    assert malformed.status_code == 404
    assert outside.status_code == 404


def test_legacy_event_location_without_day_rows_uses_event_dates(client, app):
    email, event_id, event_location_id, _ = _create_terminal_sales_event_location(
        client, app, event_name="LegacyDailySalesEvent"
    )
    with app.app_context():
        EventLocationOperatingDay.query.filter_by(
            event_location_id=event_location_id
        ).delete(synchronize_session=False)
        db.session.commit()

    with client:
        login(client, email, "pass")
        valid = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-02"
        )
        invalid = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-03"
        )

    assert valid.status_code == 200
    assert invalid.status_code == 404


def test_confirmed_event_day_cannot_be_changed_while_another_day_is_open(
    client, app
):
    email, event_id, event_location_id, product_id = (
        _create_terminal_sales_event_location(
            client, app, event_name="ConfirmedDailySalesEvent"
        )
    )

    with app.app_context():
        day_one = EventLocationOperatingDay.query.filter_by(
            event_location_id=event_location_id,
            operating_date=date(2026, 5, 1),
        ).one()
        day_one.confirmed = True
        db.session.commit()

    with client:
        login(client, email, "pass")
        blocked = client.post(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-01",
            data={f"qty_{product_id}": 4},
            follow_redirects=False,
        )
        editable = client.get(
            f"/events/{event_id}/locations/{event_location_id}/sales/add"
            "?operating_date=2026-05-02"
        )

    assert blocked.status_code == 302
    assert blocked.headers["Location"].endswith(
        f"/events/{event_id}#event-day-pane-2026-05-01"
    )
    assert editable.status_code == 200
    with client:
        event_page = client.get(f"/events/{event_id}")
        body = event_page.data.decode()
    base_path = f"/events/{event_id}/locations/{event_location_id}/sales/add"
    assert f'{base_path}?operating_date=2026-05-01' not in body
    assert f'{base_path}?operating_date=2026-05-02' in body
    with app.app_context():
        assert TerminalSale.query.filter_by(
            event_location_id=event_location_id,
            product_id=product_id,
        ).count() == 0


def test_cumulative_sales_report_includes_all_days_and_legacy_rows(client, app):
    email, event_id, event_location_id, product_id = (
        _create_terminal_sales_event_location(
            client, app, event_name="CumulativeSalesReportEvent"
        )
    )

    with app.app_context():
        sellable_amount = ProductSellableAmount(
            product_id=product_id,
            name="Each",
            quantity=1.0,
            price=2.0,
            active=True,
            is_default=True,
        )
        db.session.add(sellable_amount)
        db.session.flush()
        db.session.add_all(
            [
                TerminalSale(
                    event_location_id=event_location_id,
                    product_id=product_id,
                    quantity=3.0,
                    approval_batch_id="report-import",
                    unit_price_snapshot=2.0,
                    line_total_snapshot=6.0,
                    sold_at=datetime(2026, 5, 1, 12, 0, 0),
                ),
                TerminalSale(
                    event_location_id=event_location_id,
                    product_id=product_id,
                    quantity=2.0,
                    unit_price_snapshot=2.0,
                    line_total_snapshot=4.0,
                    sellable_amount_id=sellable_amount.id,
                    sellable_amount_name="Each",
                    sold_at=datetime(2026, 5, 2, 12, 0, 0),
                ),
                TerminalSale(
                    event_location_id=event_location_id,
                    product_id=product_id,
                    quantity=1.0,
                    unit_price_snapshot=2.0,
                    line_total_snapshot=2.0,
                    sold_at=datetime(2026, 5, 5, 12, 0, 0),
                ),
            ]
        )
        db.session.get(Product, product_id).price = 99.0
        db.session.commit()

    with client:
        login(client, email, "pass")
        response = client.get(f"/events/{event_id}/sales/cumulative")

    assert response.status_code == 200
    assert b"Cumulative Sales - CumulativeSalesReportEvent" in response.data
    product_marker = f'data-product-id="{product_id}"'.encode()
    assert response.data.count(product_marker) == 1
    assert b'data-imported-quantity="3.00"' in response.data
    assert b'data-manual-quantity="3.00"' in response.data
    assert b'data-total-quantity="6.00"' in response.data
    assert b'data-total-amount="12.00"' in response.data
    assert b"1 legacy sale row" in response.data
