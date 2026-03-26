import json
import math
import os
import re
from datetime import date
from io import BytesIO

import pytest

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationTerminalSalesSummary,
    Location,
    Product,
    TerminalSale,
    TerminalSalesResolutionState,
    User,
)
from app.routes.event_routes import (
    _apply_pending_sales,
    _apply_resolution_actions,
    _derive_summary_totals_from_details,
)
from app.utils.pos_import import (
    combine_terminal_sales_totals,
    derive_terminal_sales_quantity,
    parse_terminal_sales_number,
    group_terminal_sales_rows,
)
from tests.utils import extract_csrf_token, login


def test_apply_pending_sales_replaces_previous_entries(app):
    with app.app_context():
        event = Event(
            name="Cleanup Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Main Stand")
        event_location = EventLocation(event=event, location=location)
        product_one = Product(name="Popcorn", price=5.0, cost=2.0)
        product_two = Product(name="Soda", price=3.0, cost=1.0)

        db.session.add_all(
            [event, location, event_location, product_one, product_two]
        )
        db.session.commit()

        first_sales = [
            {
                "event_location_id": event_location.id,
                "product_id": product_one.id,
                "quantity": 10.0,
                "product_name": product_one.name,
            },
            {
                "event_location_id": event_location.id,
                "product_id": product_two.id,
                "quantity": 4.0,
                "product_name": product_two.name,
            },
        ]
        first_totals = [
            {
                "event_location_id": event_location.id,
                "source_location": "Register A",
                "total_quantity": 14.0,
                "total_amount": 100.0,
                "variance_details": {
                    "products": [
                        {
                            "product_id": product_one.id,
                            "product_name": product_one.name,
                            "quantity": 10.0,
                            "file_amount": 70.0,
                            "file_prices": [7.0],
                        }
                    ]
                },
            }
        ]

        _apply_pending_sales(first_sales, first_totals)
        db.session.commit()

        initial_sales = TerminalSale.query.filter_by(
            event_location_id=event_location.id
        ).all()
        assert {sale.product_id for sale in initial_sales} == {
            product_one.id,
            product_two.id,
        }

        second_sales = [
            {
                "event_location_id": event_location.id,
                "product_id": product_one.id,
                "quantity": 7.0,
                "product_name": product_one.name,
            }
        ]
        second_totals = [
            {
                "event_location_id": event_location.id,
                "source_location": "Register A",
                "total_quantity": 7.0,
                "total_amount": 49.0,
            }
        ]

        _apply_pending_sales(second_sales, second_totals)
        db.session.commit()

        remaining_sales = TerminalSale.query.filter_by(
            event_location_id=event_location.id
        ).all()
        assert [sale.product_id for sale in remaining_sales] == [product_one.id]
        assert remaining_sales[0].quantity == pytest.approx(7.0)

        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=event_location.id
        ).one()
        assert summary.total_quantity == pytest.approx(7.0)
        assert summary.total_amount == pytest.approx(49.0)


def test_apply_pending_sales_leaves_location_menu_unchanged(app):
    with app.app_context():
        event = Event(
            name="Menu Hold Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Suite Club")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Club Sandwich", price=12.0, cost=5.0)

        db.session.add_all([event, location, event_location, product])
        db.session.commit()

        pending_sales = [
            {
                "event_location_id": event_location.id,
                "product_id": product.id,
                "product_name": product.name,
                "quantity": 8.0,
            }
        ]

        _apply_pending_sales(pending_sales, None)
        db.session.flush()

        assert list(location.products) == []
        sale = TerminalSale.query.filter_by(
            event_location_id=event_location.id, product_id=product.id
        ).one()
        assert sale.quantity == pytest.approx(8.0)


def test_location_total_summary_rows_override_amount(app):
    with app.app_context():
        event = Event(
            name="Summary Override Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Summary Stand")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Pretzel", price=7.5, cost=3.0)

        db.session.add_all([event, location, event_location, product])
        db.session.commit()

        net_total = 120.0
        discount_total = -10.0
        override_total = net_total + discount_total
        rows = [
            {
                "location": location.name,
                "product": product.name,
                "quantity": 15.0,
                "amount": 105.0,
            },
            {
                "location": location.name,
                "is_location_total": True,
                "quantity": 15.0,
                "amount": override_total,
                "net_including_tax_total": net_total,
                "discount_total": discount_total,
            },
        ]

        grouped = group_terminal_sales_rows(rows)
        location_summary = grouped[location.name]
        assert set(location_summary["products"].keys()) == {product.name}

        pending_sales = [
            {
                "event_location_id": event_location.id,
                "product_id": product.id,
                "product_name": product.name,
                "quantity": rows[0]["quantity"],
            }
        ]
        pending_totals = [
            {
                "event_location_id": event_location.id,
                "source_location": location.name,
                "total_quantity": location_summary.get("total"),
                "total_amount": location_summary.get("total_amount"),
                "net_including_tax_total": location_summary.get(
                    "net_including_tax_total"
                ),
                "discount_total": location_summary.get("discount_total"),
            }
        ]

        _apply_pending_sales(pending_sales, pending_totals)
        db.session.commit()

        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=event_location.id
        ).one()
        assert summary.total_amount == pytest.approx(override_total)
        assert summary.total_quantity == pytest.approx(location_summary.get("total"))


def test_price_mismatch_resolution_updates_catalog_price(app):
    with app.app_context():
        event = Event(
            name="Terminal Price Update Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Concourse Stand")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Hot Dog", price=3.0, cost=1.0)

        db.session.add_all([event, location, event_location, product])
        db.session.commit()

        pending_sales = [
            {
                "event_location_id": event_location.id,
                "product_id": product.id,
                "product_name": product.name,
                "quantity": 5.0,
                "product_price": product.price,
            }
        ]

        pending_totals = [
            {
                "event_location_id": event_location.id,
                "source_location": "Register 1",
                "total_quantity": 5.0,
                "total_amount": 20.0,
                "variance_details": {
                    "products": [
                        {
                            "product_id": product.id,
                            "product_name": product.name,
                            "quantity": 5.0,
                            "file_amount": 20.0,
                            "file_prices": [4.0],
                            "app_price": product.price,
                        }
                    ]
                },
            }
        ]

        _apply_pending_sales(pending_sales, pending_totals)

        queue = [
            {
                "event_location_id": event_location.id,
                "location_name": location.name,
                "sales_location": "Register 1",
                "price_issues": [
                    {
                        "product": product.name,
                        "product_id": product.id,
                        "file_prices": [4.0],
                        "app_price": product.price,
                        "catalog_price": product.price,
                        "terminal_price": 4.0,
                        "sales_location": "Register 1",
                        "resolution": "update",
                        "selected_price": 4.0,
                        "selected_option": "terminal",
                        "target_price": 4.0,
                        "options": {"catalog": product.price, "terminal": 4.0},
                    }
                ],
                "menu_issues": [],
            }
        ]

        price_updates, menu_updates = _apply_resolution_actions({"queue": queue})
        db.session.commit()

        db.session.refresh(product)
        assert price_updates == [product.name]
        assert not menu_updates
        assert product.price == pytest.approx(4.0)

        sale = TerminalSale.query.filter_by(
            event_location_id=event_location.id, product_id=product.id
        ).one()
        assert sale.quantity == pytest.approx(5.0)

        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=event_location.id
        ).one()
        assert summary.total_amount == pytest.approx(20.0)
        assert product.price * sale.quantity == pytest.approx(20.0)


def test_apply_resolution_actions_adds_menu_entries(app):
    with app.app_context():
        event = Event(
            name="Menu Add Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Center Bar")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Craft Beer", price=9.5, cost=3.0)

        db.session.add_all([event, location, event_location, product])
        db.session.commit()

        queue = [
            {
                "event_location_id": event_location.id,
                "location_name": location.name,
                "sales_location": "CENTER BAR",
                "price_issues": [],
                "menu_issues": [
                    {
                        "product_id": product.id,
                        "product": product.name,
                        "menu_name": None,
                        "resolution": "add",
                    }
                ],
            }
        ]

        price_updates, menu_updates = _apply_resolution_actions({"queue": queue})
        db.session.flush()

        assert price_updates == []
        assert menu_updates == [f"{product.name} @ {location.name}"]
        assert product in location.products


def test_apply_resolution_actions_respects_skipped_menu_entries(app):
    with app.app_context():
        event = Event(
            name="Menu Skip Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Party Deck")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Party Platter", price=25.0, cost=10.0)

        db.session.add_all([event, location, event_location, product])
        db.session.commit()

        queue = [
            {
                "event_location_id": event_location.id,
                "location_name": location.name,
                "sales_location": "PARTY DECK",
                "price_issues": [],
                "menu_issues": [
                    {
                        "product_id": product.id,
                        "product": product.name,
                        "menu_name": None,
                        "resolution": "skip",
                    }
                ],
            }
        ]

        _apply_resolution_actions({"queue": queue})
        db.session.flush()

        assert product not in location.products


@pytest.fixture
def terminal_sales_net_only_rows():
    return [
        {
            "location": "Main Stand",
            "product": "Popcorn",
            "quantity": 10.0,
            "net_including_tax_total": 95.0,
            "discount_total": 5.0,
        }
    ]


def test_apply_pending_sales_uses_net_total_when_amount_missing(
    app, terminal_sales_net_only_rows
):
    with app.app_context():
        event = Event(
            name="Net Total Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Main Stand")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Popcorn", price=10.0, cost=4.0)

        db.session.add_all([event, location, event_location, product])
        db.session.commit()

        grouped = group_terminal_sales_rows(terminal_sales_net_only_rows)
        location_summary = grouped["Main Stand"]
        net_total = sum(
            row.get("net_including_tax_total", 0.0)
            for row in terminal_sales_net_only_rows
        )
        discount_total = sum(
            row.get("discount_total") or 0.0
            for row in terminal_sales_net_only_rows
        )
        expected_total = net_total + discount_total

        pending_sales = [
            {
                "event_location_id": event_location.id,
                "product_id": product.id,
                "product_name": product.name,
                "quantity": terminal_sales_net_only_rows[0]["quantity"],
            }
        ]
        pending_totals = [
            {
                "event_location_id": event_location.id,
                "source_location": "Main Stand",
                "total_quantity": location_summary.get("total"),
                "total_amount": location_summary.get("total_amount"),
                "net_including_tax_total": location_summary.get(
                    "net_including_tax_total"
                ),
                "discount_total": location_summary.get("discount_total"),
                "variance_details": None,
            }
        ]

        _apply_pending_sales(pending_sales, pending_totals)
        db.session.commit()

        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=event_location.id
        ).one()
        assert summary.total_amount == pytest.approx(expected_total)


def test_apply_pending_sales_prefers_provided_total_amount(app):
    with app.app_context():
        event = Event(
            name="Provided Amount Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Main Stand")
        event_location = EventLocation(event=event, location=location)

        db.session.add_all([event, location, event_location])
        db.session.commit()

        pending_totals = [
            {
                "event_location_id": event_location.id,
                "source_location": "Main Stand",
                "total_quantity": 10.0,
                "total_amount": 1053.0,
                "net_including_tax_total": 1053.0,
                "discount_total": 50.0,
                "variance_details": None,
            }
        ]

        _apply_pending_sales([], pending_totals)
        db.session.commit()

        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=event_location.id
        ).one()
        assert summary.total_amount == pytest.approx(1053.0)


def test_parse_terminal_sales_number_strips_locale_prefixes():
    assert parse_terminal_sales_number("CA\u00A01,234.56") == pytest.approx(1234.56)
    assert parse_terminal_sales_number("C$\u00A0-98.76") == pytest.approx(-98.76)
    assert parse_terminal_sales_number("\u00A0ca$\u00A042") == pytest.approx(42.0)


def test_group_terminal_sales_rows_handles_locale_currency_totals():
    net_total = parse_terminal_sales_number("CA\u00A0143.45")
    discount_total = parse_terminal_sales_number("C$\u00A0-23.45")
    rows = [
        {
            "location": "Main Stand",
            "product": "Popcorn",
            "quantity": parse_terminal_sales_number("2"),
            "net_including_tax_total": net_total,
            "discount_total": discount_total,
        }
    ]

    grouped = group_terminal_sales_rows(rows)
    summary = grouped["Main Stand"]

    assert summary["net_including_tax_total"] == pytest.approx(net_total)
    assert summary["discount_total"] == pytest.approx(discount_total)
    assert summary["total_amount"] == pytest.approx(net_total + discount_total)


def test_derive_terminal_sales_quantity_uses_amount_when_quantity_missing():
    quantity = None
    derived = derive_terminal_sales_quantity(
        quantity,
        price=5.25,
        amount=5.25,
        net_including_tax_total=None,
        discounts_total=None,
    )
    assert derived == pytest.approx(1.0)


def test_derive_terminal_sales_quantity_handles_zero_quantity_with_net():
    quantity = 0.0
    derived = derive_terminal_sales_quantity(
        quantity,
        price=4.0,
        amount=None,
        net_including_tax_total=9.0,
        discounts_total=-1.0,
    )
    assert derived == pytest.approx(2.0)

def test_group_terminal_sales_rows_prefers_net_plus_discount_over_raw_amount():
    rows = [
        {
            "location": "Main Stand",
            "product": "Popcorn",
            "quantity": 5.0,
            "amount": 125.0,
            "net_including_tax_total": 100.0,
            "discount_total": 10.0,
        },
        {
            "location": "Main Stand",
            "product": "Soda",
            "quantity": 3.0,
            "amount": 45.0,
        },
    ]

    grouped = group_terminal_sales_rows(rows)
    summary = grouped["Main Stand"]

    # Even though raw totals are available, prefer the net total plus any discounts.
    assert summary["total_amount"] == pytest.approx(110.0)


def test_group_terminal_sales_rows_handles_comma_decimal_quantities():
    rows = [
        {
            "location": "Main Stand",
            "product": "Popcorn",
            "quantity": "1,0000",
        }
    ]

    grouped = group_terminal_sales_rows(rows)
    location_data = grouped["Main Stand"]
    product_data = location_data["products"]["Popcorn"]

    assert product_data["quantity"] == pytest.approx(1.0)
    assert location_data["total"] == pytest.approx(1.0)


def test_group_terminal_sales_rows_preserves_spreadsheet_unit_prices():
    rows = [
        {
            "location": "Prairie Grill",
            "product": "Burger - Double Hamburger",
            "quantity": 1.0,
            "price": 7.75,
            "raw_price": 9.75,
            "amount": 8.7,
            "net_including_tax_total": 7.75,
            "discount_total": 0.0,
        }
    ]

    grouped = group_terminal_sales_rows(rows)
    product_summary = grouped["Prairie Grill"]["products"]["Burger - Double Hamburger"]

    spreadsheet_prices = product_summary.get("spreadsheet_prices")
    assert spreadsheet_prices is not None
    assert any(math.isclose(value, 9.75, abs_tol=0.01) for value in spreadsheet_prices)


def test_terminal_sales_stays_on_products_until_finish(app, client):
    with app.app_context():
        event = Event(
            name="Terminal Test Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Main Stand")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Bottled Water", price=3.5, cost=1.0)
        db.session.add_all([event, location, event_location, product])
        db.session.commit()

        payload = json.dumps(
            {
                "rows": [
                    {
                        "location": location.name,
                        "product": product.name,
                        "quantity": 2,
                        "price": float(product.price),
                    }
                ],
                "filename": "terminal.xlsx",
            }
        )

        mapping_field = f"mapping-{event_location.id}"
        event_id = event.id
        event_location_id = event_location.id
        location_name = location.name
        product_id = product.id

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "map",
                "payload": payload,
                "stage": "locations",
                mapping_field: location_name,
                "navigate": "next",
            },
        )

        assert response.status_code == 200
        body = response.data.decode()
        assert 'name="stage" value="products"' in body
        assert (
            "All products in the uploaded file have been matched automatically."
            in body
        )
        assert 'data-role="toggle-product-preview"' in body
        assert 'data-role="product-mapping-preview"' in body
        assert f"(ID: {product_id})" in body

        with app.app_context():
            assert TerminalSale.query.count() == 0

        finish_response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "map",
                "payload": payload,
                "stage": "products",
                mapping_field: location_name,
                "navigate": "finish",
            },
            follow_redirects=False,
        )

        assert finish_response.status_code == 200
        finish_body = finish_response.data.decode()
        assert 'name="step" value="confirm_menus"' in finish_body, finish_body
        assert "Review Menu Additions" in finish_body

        menu_key = f"{event_location_id}:{product_id}"
        state_token_match = re.search(
            r'name="state_token" value="([^"]+)"', finish_body
        )
        assert state_token_match is not None
        state_token_value = state_token_match.group(1)

        with app.app_context():
            assert TerminalSale.query.count() == 0

        confirm_response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "confirm_menus",
                "state_token": state_token_value,
                "menu_additions": menu_key,
                "action": "finish",
            },
            follow_redirects=False,
        )

        assert confirm_response.status_code == 302
        assert confirm_response.headers["Location"].endswith(f"/events/{event_id}")

    with app.app_context():
        sales = TerminalSale.query.filter_by(
            event_location_id=event_location_id
        ).all()
        assert len(sales) == 1
        assert sales[0].product_id == product_id
        location = EventLocation.query.get(event_location_id).location
        assert any(p.id == product_id for p in location.products)


def test_terminal_sales_upload_saves_locale_currency_totals(app, client):
    net_total = parse_terminal_sales_number("CA\u00A0143.45")
    discount_total = parse_terminal_sales_number("CA\u00A0-23.45")
    with app.app_context():
        event = Event(
            name="Locale Currency Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Locale Stand")
        product = Product(name="Locale Popcorn", price=60.0, cost=20.0)
        location.products.append(product)
        event_location = EventLocation(event=event, location=location)
        db.session.add_all([event, location, product, event_location])
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        mapping_field = f"mapping-{event_location.id}"

    quantity_value = parse_terminal_sales_number("2")
    price_value = parse_terminal_sales_number("C$\u00A060.00")
    amount_value = parse_terminal_sales_number("CA\u00A0120.00")
    payload = json.dumps(
        {
            "rows": [
                {
                    "location": "Locale Stand",
                    "product": "Locale Popcorn",
                    "quantity": quantity_value,
                    "price": price_value,
                    "amount": amount_value,
                    "net_including_tax_total": net_total,
                    "discount_total": discount_total,
                }
            ],
            "filename": "terminal.xlsx",
        }
    )

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        finish_response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "map",
                "payload": payload,
                "stage": "locations",
                mapping_field: "Locale Stand",
                "navigate": "finish",
            },
            follow_redirects=False,
        )

        assert finish_response.status_code == 302

        confirm_response = client.get(
            f"/events/{event_id}/locations/{event_location_id}/confirm",
            follow_redirects=False,
        )
        assert confirm_response.status_code == 200
        assert b"Terminal File Total" in confirm_response.data
        assert b"$120.00" in confirm_response.data

    with app.app_context():
        summary = db.session.get(
            EventLocationTerminalSalesSummary, event_location_id
        )
        assert summary is not None
        assert summary.total_quantity == pytest.approx(2.0)
        assert summary.total_amount == pytest.approx(net_total + discount_total)
        assert summary.source_location == "Locale Stand"

        sales = TerminalSale.query.filter_by(
            event_location_id=event_location_id
        ).all()
        assert len(sales) == 1
        assert sales[0].product.name == "Locale Popcorn"
        assert sales[0].quantity == pytest.approx(2.0)


def test_terminal_sales_multiple_products_generate_price_issues(app, client):
    with app.app_context():
        event = Event(
            name="Multiple Products Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Multi Stand")
        event_location = EventLocation(event=event, location=location)
        product_one = Product(name="Pretzel", price=5.0, cost=2.0)
        product_two = Product(name="Nachos", price=7.0, cost=3.0)
        db.session.add_all(
            [event, location, event_location, product_one, product_two]
        )
        db.session.commit()
        event_id = event.id
        mapping_field = f"mapping-{event_location.id}"
        location_name = location.name
        product_one_name = product_one.name
        product_two_name = product_two.name

    payload = json.dumps(
        {
            "rows": [
                {
                    "location": location_name,
                    "product": product_one_name,
                    "quantity": 2.0,
                    "price": 6.0,
                    "amount": 12.0,
                },
                {
                    "location": location_name,
                    "product": product_two_name,
                    "quantity": 3.0,
                    "price": 8.0,
                    "amount": 24.0,
                },
            ],
            "filename": "terminal.xlsx",
        }
    )

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "map",
                "payload": payload,
                "stage": "locations",
                mapping_field: location_name,
                "navigate": "finish",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).one()
        state_row = TerminalSalesResolutionState.query.filter_by(
            event_id=event_id, user_id=admin_user.id
        ).one()
        assert isinstance(state_row.payload, dict)
        pending_sales = state_row.payload.get("pending_sales") or []
        assert len(pending_sales) == 2
        assert {
            entry.get("product_name") for entry in pending_sales
        } == {product_one_name, product_two_name}

        issue_queue = state_row.payload.get("queue") or []
        assert len(issue_queue) == 1
        price_issues = issue_queue[0].get("price_issues") or []
        assert len(price_issues) == 2
        assert {
            issue.get("product") for issue in price_issues
        } == {product_one_name, product_two_name}


def test_terminal_sales_zero_price_comp_does_not_queue_mismatch(app, client):
    with app.app_context():
        event = Event(
            name="Zero Price Comp Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Comp Stand")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Soft Drink", price=6.0, cost=2.0)
        db.session.add_all([event, location, event_location, product])
        db.session.commit()
        event_id = event.id
        mapping_field = f"mapping-{event_location.id}"
        location_name = location.name
        product_name = product.name

    payload = json.dumps(
        {
            "rows": [
                {
                    "location": location_name,
                    "product": product_name,
                    "quantity": 2.0,
                    "price": 6.0,
                    "amount": 12.0,
                },
                {
                    "location": location_name,
                    "product": product_name,
                    "quantity": 1.0,
                    "price": 0.0,
                    "amount": 0.0,
                },
            ],
            "filename": "terminal.xlsx",
        }
    )

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "map",
                "payload": payload,
                "stage": "locations",
                mapping_field: location_name,
                "navigate": "finish",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).one()
        state_row = TerminalSalesResolutionState.query.filter_by(
            event_id=event_id, user_id=admin_user.id
        ).one()
        issue_queue = state_row.payload.get("queue") or []
        if issue_queue:
            price_issues = issue_queue[0].get("price_issues") or []
            assert price_issues == []
        else:
            assert issue_queue == []

        pending_totals = state_row.payload.get("pending_totals") or []
        assert len(pending_totals) == 1
        variance_details = pending_totals[0].get("variance_details") or {}
        price_mismatches = variance_details.get("price_mismatches") or []
        assert len(price_mismatches) == 1
        mismatch_entry = price_mismatches[0]
        assert mismatch_entry.get("product_name") == product_name
        file_prices = mismatch_entry.get("file_prices") or []
        assert any(math.isclose(value, 0.0, abs_tol=0.01) for value in file_prices)
        assert any(math.isclose(value, product.price, abs_tol=0.01) for value in file_prices)


def test_terminal_sales_price_matching_uses_sell_price_not_invoice_sale_price(
    app, client
):
    with app.app_context():
        event = Event(
            name="Sell Price Source Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Sell Price Stand")
        event_location = EventLocation(event=event, location=location)
        product = Product(
            name="Source Guard Hot Dog",
            price=9.0,
            invoice_sale_price=3.5,
            cost=2.0,
        )
        db.session.add_all([event, location, event_location, product])
        db.session.commit()
        event_id = event.id
        mapping_field = f"mapping-{event_location.id}"
        location_name = location.name
        product_name = product.name

    payload = json.dumps(
        {
            "rows": [
                {
                    "location": location_name,
                    "product": product_name,
                    "quantity": 2.0,
                    "price": 9.0,
                    "amount": 18.0,
                }
            ],
            "filename": "terminal.xlsx",
        }
    )

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "map",
                "payload": payload,
                "stage": "locations",
                mapping_field: location_name,
                "navigate": "finish",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).one()
        state_row = TerminalSalesResolutionState.query.filter_by(
            event_id=event_id, user_id=admin_user.id
        ).one()
        issue_queue = state_row.payload.get("queue") or []
        if issue_queue:
            price_issues = issue_queue[0].get("price_issues") or []
            assert price_issues == []
        else:
            assert issue_queue == []


def test_terminal_sales_totals_without_unit_price_queue_price_issue(app, client):
    with app.app_context():
        event = Event(
            name="Totals Without Price Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Totals Stand")
        event_location = EventLocation(event=event, location=location)
        product = Product(name="Bottled Water", price=12.0, cost=4.0)
        db.session.add_all([event, location, event_location, product])
        db.session.commit()
        event_id = event.id
        mapping_field = f"mapping-{event_location.id}"
        location_name = location.name
        product_name = product.name

    payload = json.dumps(
        {
            "rows": [
                {
                    "location": location_name,
                    "product": product_name,
                    "quantity": 0.0,
                },
                {
                    "location": location_name,
                    "is_location_total": True,
                    "quantity": 5.0,
                    "amount": 50.0,
                },
            ],
            "filename": "terminal.xlsx",
        }
    )

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        response = client.post(
            f"/events/{event_id}/terminal-sales",
            data={
                "step": "map",
                "payload": payload,
                "stage": "locations",
                mapping_field: location_name,
                "navigate": "finish",
            },
            follow_redirects=False,
        )

        assert response.status_code == 200

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).one()
        state_row = TerminalSalesResolutionState.query.filter_by(
            event_id=event_id, user_id=admin_user.id
        ).one()
        assert isinstance(state_row.payload, dict)
        issue_queue = state_row.payload.get("queue") or []
        assert len(issue_queue) == 1
        price_issues = issue_queue[0].get("price_issues") or []
        assert len(price_issues) == 1
        issue = price_issues[0]
        assert issue.get("product") == product_name
        assert issue.get("terminal_price") == pytest.approx(10.0)


def test_terminal_sales_excel_price_mismatch_detected(app, client, monkeypatch):
    class MockSheet:
        def __init__(self, rows):
            self._rows = rows
            self.nrows = len(rows)
            self.ncols = max((len(row) for row in rows), default=0)

        def cell_value(self, row_idx, col_idx):
            row = self._rows[row_idx]
            if col_idx < len(row):
                return row[col_idx]
            return None

    class MockBook:
        def __init__(self, rows):
            self._sheet = MockSheet(rows)

        def sheet_by_index(self, index):
            if index != 0:
                raise IndexError("Only one sheet available")
            return self._sheet

        def release_resources(self):
            return None

    excel_rows = [
        ["Main Stand", None, None, None, None, None, None, None, None],
        [
            1,
            "Hot Dog",
            9.0,
            None,
            1.0,
            9.0,
            None,
            10.0,
            0.0,
        ],
    ]

    import xlrd

    monkeypatch.setattr(xlrd, "open_workbook", lambda path: MockBook(excel_rows))

    with app.app_context():
        event = Event(
            name="Excel Price Mismatch Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Main Stand")
        product = Product(name="Hot Dog", price=10.0, cost=3.0)
        event_location = EventLocation(event=event, location=location)
        db.session.add_all([event, location, product, event_location])
        db.session.commit()
        event_id = event.id
        location_name = location.name
        product_name = product.name

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        upload_page = client.get(f"/events/{event_id}/terminal-sales")
        csrf_token = extract_csrf_token(upload_page, required=False)
        form_data = {"program": "idealpos"}
        if csrf_token:
            form_data["csrf_token"] = csrf_token
        form_data["file"] = (BytesIO(b"stub"), "terminal.xls")

        response = client.post(
            f"/events/{event_id}/terminal-sales",
            data=form_data,
            content_type="multipart/form-data",
        )

        assert response.status_code == 200

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).one()
        state_row = TerminalSalesResolutionState.query.filter_by(
            event_id=event_id, user_id=admin_user.id
        ).one()
        stored_rows = (
            (state_row.payload or {})
            .get("payload", {})
            .get("rows", [])
        )

        assert stored_rows, "Expected uploaded rows to be stored in state"

        grouped = group_terminal_sales_rows(stored_rows)
        location_summary = grouped[location_name]
        product_summary = location_summary["products"][product_name]
        price_candidates = product_summary["prices"]

        assert price_candidates, "Expected to capture the POS price from the spreadsheet"
        assert any(
            math.isclose(price, 9.0, abs_tol=0.01) for price in price_candidates
        )

        catalog_price = float(
            Product.query.filter_by(name=product_name).one().price or 0.0
        )
        assert not all(
            math.isclose(price, catalog_price, abs_tol=0.01) for price in price_candidates
        ), "The uploaded price should trigger a mismatch against the catalog"


def test_terminal_sales_raw_price_triggers_discrepancy_when_totals_match_catalog():
    location_name = "Main Stand"
    product_name = "Discrepancy Hot Dog"
    catalog_price = 10.0

    rows = [
        {
            "location": location_name,
            "product": product_name,
            "quantity": 2.0,
            "price": catalog_price,
            "raw_price": 9.0,
            "amount": 18.0,
            "net_including_tax_total": 20.0,
            "discount_total": 0.0,
        }
    ]

    grouped = group_terminal_sales_rows(rows)
    location_summary = grouped[location_name]
    product_summary = location_summary["products"][product_name]

    prices = product_summary["prices"]
    assert len(prices) == 2
    assert any(math.isclose(price, catalog_price, abs_tol=0.01) for price in prices)
    assert any(math.isclose(price, 9.0, abs_tol=0.01) for price in prices)

    spreadsheet_prices = product_summary["spreadsheet_prices"]
    assert spreadsheet_prices
    assert any(math.isclose(price, 9.0, abs_tol=0.01) for price in spreadsheet_prices)

    combined_total_value = combine_terminal_sales_totals(
        product_summary.get("net_including_tax_total"),
        product_summary.get("discount_total"),
    )
    quantity_value = product_summary.get("quantity")

    derived_unit_price = None
    if (
        combined_total_value is not None
        and quantity_value
        and abs(quantity_value) > 1e-9
    ):
        derived_unit_price = float(combined_total_value) / float(quantity_value)

    file_price_candidates = [price for price in prices if price is not None]
    price_candidates: list[float] = []
    if derived_unit_price is not None:
        price_candidates.append(derived_unit_price)
    price_candidates.extend(file_price_candidates)

    file_amount = product_summary.get("amount")
    fallback_amount_price = None
    if file_amount is not None and quantity_value:
        try:
            fallback_amount_price = float(file_amount) / float(quantity_value)
        except (TypeError, ValueError, ZeroDivisionError):
            fallback_amount_price = None

    if (
        derived_unit_price is None
        and not file_price_candidates
        and fallback_amount_price is not None
    ):
        price_candidates.append(fallback_amount_price)

    if not price_candidates and fallback_amount_price is not None:
        price_candidates = [fallback_amount_price]

    assert any(math.isclose(price, catalog_price, abs_tol=0.01) for price in price_candidates)
    assert any(math.isclose(price, 9.0, abs_tol=0.01) for price in price_candidates)
    assert not all(
        math.isclose(price, catalog_price, abs_tol=0.01) for price in price_candidates
    )


def test_derive_summary_totals_handles_string_details():
    details = {
        "products": [
            {
                "quantity": "2",
                "file_amount": "10.5",
                "file_prices": ["5.25"],
            }
        ],
        "unmapped_products": [
            {
                "product_name": "Unknown",
                "quantity": 1,
                "file_prices": [3.0],
            }
        ],
    }

    quantity, amount = _derive_summary_totals_from_details(details)

    assert quantity == pytest.approx(3.0)
    assert amount == pytest.approx(13.5)

    string_quantity, string_amount = _derive_summary_totals_from_details(
        json.dumps(details)
    )

    assert string_quantity == pytest.approx(3.0)
    assert string_amount == pytest.approx(13.5)

    assert _derive_summary_totals_from_details("not json") == (None, None)


def test_apply_pending_sales_normalizes_string_variance_details(app):
    with app.app_context():
        event = Event(
            name="Variance Normalization Event",
            start_date=date.today(),
            end_date=date.today(),
        )
        location = Location(name="Variance Stand")
        event_location = EventLocation(event=event, location=location)
        db.session.add_all([event, location, event_location])
        db.session.commit()

        variance_details = {
            "products": [
                {
                    "product_id": None,
                    "product_name": "Snacks",
                    "quantity": "2",
                    "file_amount": "10.5",
                    "file_prices": ["5.25"],
                }
            ],
            "unmapped_products": [
                {
                    "product_name": "Unknown",
                    "quantity": "1",
                    "file_amount": None,
                    "file_prices": ["3.0"],
                }
            ],
        }

        _apply_pending_sales(
            [],
            pending_totals=[
                {
                    "event_location_id": event_location.id,
                    "total_quantity": None,
                    "total_amount": None,
                    "variance_details": json.dumps(variance_details),
                }
            ],
            link_products_to_locations=False,
        )

        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=event_location.id
        ).one()

        assert isinstance(summary.variance_details, dict)

        derived_quantity = summary.total_quantity
        derived_amount = summary.total_amount

        assert derived_quantity == pytest.approx(3.0)
        assert derived_amount == pytest.approx(13.5)

        product_entry = summary.variance_details.get("products", [])[0]
        assert product_entry.get("quantity") == pytest.approx(2.0)
        assert product_entry.get("file_amount") == pytest.approx(10.5)
        assert product_entry.get("file_prices") == [pytest.approx(5.25)]
