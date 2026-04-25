from __future__ import annotations

import json
import re
from datetime import date
from html import unescape

from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

from app import db
from app.models import Event, EventLocation, Item, Location, User
from tests.permission_helpers import grant_event_permissions, grant_permissions
from tests.utils import login


def _seed_event_user(app, *, email: str, with_item: bool = False):
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Main Bar")
        event = Event(
            name="Permission Test Event",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 2),
            event_type="inventory",
        )
        db.session.add_all([user, location, event])
        db.session.flush()
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        item = None
        if with_item:
            item = Item(name="Countable Item", base_unit="each")
            db.session.add(item)
        db.session.commit()
        return {
            "email": user.email,
            "event_id": event.id,
            "event_location_id": event_location.id,
            "location_id": location.id,
            "item_id": item.id if item is not None else None,
        }


def _create_terminal_product(client, *, name: str = "Uploaded Product") -> int:
    response = client.post(
        "/products/ajax/create",
        data={
            "name": name,
            "price": "7.50",
            "cost": "7.50",
            "recipe_yield_quantity": "1",
            "recipe_yield_unit": "",
        },
    )
    payload = response.get_json()
    assert payload and payload.get("success"), payload
    return int(payload["product"]["id"])


def test_events_list_hides_create_and_reports_without_permission(client, app):
    seeded = _seed_event_user(app, email="events-view@example.com")

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.view",
            group_name=f"Events View Only {user.email}",
            description="View events without create or report permissions.",
        )

    with client:
        login(client, seeded["email"], "pass")
        response = client.get("/events")
        body = response.data.decode()

    assert response.status_code == 200
    assert 'data-bs-target="#createEventModal"' not in body
    assert 'id="createEventModal"' not in body
    assert "Create Event" not in body
    assert "Event Terminal Sales Report" not in body


def test_event_detail_hides_management_controls_without_permission(client, app):
    seeded = _seed_event_user(app, email="event-detail@example.com")

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.view",
            group_name=f"Event Detail View Only {user.email}",
            description="View event detail without management permissions.",
        )

    with client:
        login(client, seeded["email"], "pass")
        response = client.get(f"/events/{seeded['event_id']}")
        body = response.data.decode()

    assert response.status_code == 200
    assert "/add_location" not in body
    assert "/sales/upload" not in body
    assert "/close" not in body
    assert 'id="opening-counts-form"' not in body
    assert "Stand Sheet" not in body
    assert "Count Sheet" not in body
    assert "Scan Counts" not in body
    assert "Enter Sales" not in body
    assert f"/events/{seeded['event_id']}/locations/{seeded['event_location_id']}/confirm" not in body
    assert "/undo_confirm_location" not in body


def test_terminal_sales_upload_hides_product_creation_without_products_create_permission(
    client, app
):
    seeded = _seed_event_user(app, email="upload-no-product-create@example.com")

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_event_permissions(user, include_product_create=False)

    payload = {
        "rows": [
            {
                "location": "Main Bar",
                "product": "Mystery Drink",
                "quantity": 3,
                "price": 4.5,
            }
        ],
        "filename": "terminal_sales.xlsx",
    }

    with client:
        login(client, seeded["email"], "pass")
        response = client.post(
            f"/events/{seeded['event_id']}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(payload),
                f"mapping-{seeded['event_location_id']}": "Main Bar",
            },
            follow_redirects=True,
        )
        body = response.data.decode()

    assert response.status_code == 200
    assert "Match Unknown Products" in body
    assert "Create a new product" not in body
    assert 'id="terminalCreateProductModal"' not in body
    assert 'data-action="create"' not in body


def test_terminal_sales_upload_hides_quick_add_item_without_items_create_permission(
    client, app
):
    seeded = _seed_event_user(app, email="upload-no-item-create@example.com", with_item=True)

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_event_permissions(user)

    payload = {
        "rows": [
            {
                "location": "Main Bar",
                "product": "Mystery Drink",
                "quantity": 3,
                "price": 4.5,
            }
        ],
        "filename": "terminal_sales.xlsx",
    }

    with client:
        login(client, seeded["email"], "pass")
        initial_response = client.post(
            f"/events/{seeded['event_id']}/sales/upload",
            data={
                "step": "map",
                "payload": json.dumps(payload),
                f"mapping-{seeded['event_location_id']}": "Main Bar",
            },
            follow_redirects=True,
        )
        initial_body = initial_response.data.decode()
        assert "Match Unknown Products" in initial_body
        token_match = re.search(r'name="state_token" value="([^"]+)"', initial_body)
        assert token_match
        state_token = unescape(token_match.group(1))

        created_product_id = _create_terminal_product(client, name="Mystery Drink")

        resolution_response = client.post(
            f"/events/{seeded['event_id']}/sales/upload",
            data=MultiDict([
                ("step", "map"),
                ("stage", "products"),
                ("product-resolution-step", "1"),
                ("countable-selection-step", "1"),
                ("state_token", state_token),
                ("payload", json.dumps(payload)),
                (f"mapping-{seeded['event_location_id']}", "Main Bar"),
                ("product-match-0", str(created_product_id)),
                ("created_product_ids", str(created_product_id)),
            ]),
            follow_redirects=True,
        )
        body = resolution_response.data.decode()

    assert resolution_response.status_code == 200
    assert 'data-countable-action="quick-add-item"' not in body
    assert "Create new item" not in body
    assert 'id="newItemModal"' not in body
