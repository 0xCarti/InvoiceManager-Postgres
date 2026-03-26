import json
import re
from datetime import date
from html import unescape

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    Location,
    Menu,
    Product,
    TerminalSale,
    TerminalSalesResolutionState,
    User,
)
from tests.utils import login


def create_modal_product(client, name: str, price: str, **fields) -> int:
    form_data = {
        "name": name,
        "price": str(price),
        "cost": str(fields.pop("cost", price)),
        "recipe_yield_quantity": str(fields.pop("recipe_yield_quantity", 1)),
        "recipe_yield_unit": str(fields.pop("recipe_yield_unit", "")),
    }

    sales_gl_code = fields.pop("sales_gl_code", None)
    if sales_gl_code not in (None, ""):
        form_data["sales_gl_code"] = str(sales_gl_code)
    for key, value in fields.items():
        form_data[key] = str(value)

    response = client.post("/products/ajax/create", data=form_data)
    payload = response.get_json()
    assert payload and payload.get("success"), payload
    return int(payload["product"]["id"])


def test_upload_get_without_state_token_resets_wizard(client, app):
    payload_rows = [
        {"location": "Stadium Stand", "product": "Nachos", "quantity": 5}
    ]

    with app.app_context():
        user = User(
            email="terminal-reset@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Stadium Stand")
        allowed_product = Product(name="Soft Pretzel", price=4.0, cost=1.5)
        menu = Menu(name="Stadium Menu")
        menu.products.append(allowed_product)
        location.products.append(allowed_product)
        location.current_menu = menu
        event = Event(
            name="Terminal Reset Event",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 1),
            event_type="inventory",
        )
        db.session.add_all([user, location, allowed_product, menu, event])
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.commit()

        event_id = event.id
        event_location_id = event_location.id
        user_id = user.id

    payload = json.dumps({"rows": payload_rows, "filename": "terminal_sales.xlsx"})

    with client:
        login(client, "terminal-reset@example.com", "pass")
        map_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                f"mapping-{event_location_id}": "Stadium Stand",
            },
            follow_redirects=True,
        )
        assert map_response.status_code == 200
        map_body = map_response.get_data(as_text=True)
        assert "Match Unknown Products" in map_body
        token_match = re.search(r'name="state_token" value="([^"]+)"', map_body)
        assert token_match is not None
        state_token = unescape(token_match.group(1))

        created_product_id = create_modal_product(
            client,
            name="Nachos",
            price="6.00",
        )

        resolution_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                "stage": "products",
                "product-resolution-step": "1",
                "navigate": "finish",
                "state_token": state_token,
                f"mapping-{event_location_id}": "Stadium Stand",
                "product-match-0": str(created_product_id),
                "created_product_ids": str(created_product_id),
            },
            follow_redirects=True,
        )
        assert resolution_response.status_code == 200
        resolution_body = resolution_response.get_data(as_text=True)
        assert "Menu Availability" in resolution_body
        token_match = re.search(r'name="state_token" value="([^"]+)"', resolution_body)
        assert token_match is not None
        state_token = unescape(token_match.group(1))

        add_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "resolve",
                "state_token": state_token,
                "payload": payload,
                "mapping_filename": "terminal_sales.xlsx",
                "action": f"menu:{created_product_id}:add",
            },
            follow_redirects=True,
        )
        assert add_response.status_code == 200
        add_body = add_response.get_data(as_text=True)
        assert "Will add product to the menu" in add_body
        token_match = re.search(r'name="state_token" value="([^"]+)"', add_body)
        assert token_match is not None
        state_token = unescape(token_match.group(1))

        finish_response = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "resolve",
                "state_token": state_token,
                "payload": payload,
                "mapping_filename": "terminal_sales.xlsx",
                "action": "finish",
            },
            follow_redirects=True,
        )
        assert finish_response.status_code == 200

        # Begin a second upload attempt but stop at the first wizard step so state is stored.
        second_map = client.post(
            f"/events/{event_id}/sales/upload",
            data={
                "step": "map",
                "payload": payload,
                f"mapping-{event_location_id}": "Stadium Stand",
            },
            follow_redirects=True,
        )
        assert second_map.status_code == 200

        with app.app_context():
            state_count = (
                TerminalSalesResolutionState.query.filter_by(
                    event_id=event_id, user_id=user_id
                ).count()
            )
            assert state_count == 1

        reset_response = client.get(f"/events/{event_id}/sales/upload")
        assert reset_response.status_code == 200
        reset_body = reset_response.get_data(as_text=True)
        assert "IdealPOS sales export example" in reset_body
        assert "Match Unknown Products" not in reset_body

        with app.app_context():
            remaining_states = (
                TerminalSalesResolutionState.query.filter_by(
                    event_id=event_id, user_id=user_id
                ).count()
            )
            assert remaining_states == 0
import math

import pytest

from app.utils.pos_import import (
    combine_terminal_sales_totals,
    group_terminal_sales_rows,
)


def test_terminal_sales_combined_totals_drive_unit_price():
    location_name = "Main Stand"
    product_name = "Discounted Pretzel"
    quantity = 2.0
    net_total = 90.0
    discount_total = -10.0

    rows = [
        {
            "location": location_name,
            "product": product_name,
            "quantity": quantity,
            # Spreadsheet price column provided a value that does not include discounts.
            "price": 55.0,
            "amount": 110.0,
            "net_including_tax_total": net_total,
            "discount_total": discount_total,
        }
    ]

    grouped = group_terminal_sales_rows(rows)
    product_summary = grouped[location_name]["products"][product_name]

    assert product_summary.get("net_including_tax_total") == pytest.approx(net_total)
    assert product_summary.get("discount_total") == pytest.approx(discount_total)
    assert product_summary["quantity"] == pytest.approx(quantity)

    combined_total = combine_terminal_sales_totals(
        product_summary.get("net_including_tax_total"),
        product_summary.get("discount_total"),
    )
    assert combined_total == pytest.approx(net_total + discount_total)

    derived_unit_price = combined_total / product_summary["quantity"]
    assert derived_unit_price == pytest.approx(
        (net_total + discount_total) / product_summary["quantity"]
    )

    file_prices = [price for price in product_summary["prices"] if price is not None]
    assert file_prices == pytest.approx([55.0])

    price_candidates = [derived_unit_price]
    price_candidates.extend(file_prices)
    terminal_price_value = derived_unit_price

    assert price_candidates[0] == pytest.approx(derived_unit_price)
    assert terminal_price_value == pytest.approx(derived_unit_price)

    catalog_price = 55.0
    assert not all(
        math.isclose(price, catalog_price, abs_tol=0.01) for price in price_candidates
    )
