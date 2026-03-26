from datetime import date
from uuid import uuid4

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    User,
)
from tests.utils import login


def _setup_event(app, *, event_type="inventory", closed=False):
    with app.app_context():
        email = f"scanner_{uuid4().hex[:8]}@example.com"
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name=f"Scan Booth {uuid4().hex[:6]}")
        upc = f"{uuid4().int % 10**12:012d}"
        item = Item(name=f"Scannable Item {uuid4().hex[:6]}", base_unit="each", upc=upc)
        db.session.add_all([user, location, item])
        db.session.commit()

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
                location_id=location.id,
                item_id=item.id,
                expected_count=5,
            )
        )

        event = Event(
            name="Inventory Scan",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            event_type=event_type,
            closed=closed,
        )
        db.session.add(event)
        db.session.commit()

        event_location = EventLocation(event_id=event.id, location_id=location.id)
        db.session.add(event_location)
        db.session.commit()

        return {
            "email": email,
            "event_id": event.id,
            "location_id": location.id,
            "event_location_id": event_location.id,
            "item_upc": upc,
            "item_id": item.id,
        }


def test_scan_counts_rejects_non_inventory(client, app):
    context = _setup_event(app, event_type="other")
    url = f"/events/{context['event_id']}/locations/{context['location_id']}/scan_counts"

    with client:
        login(client, context["email"], "pass")
        get_resp = client.get(url)
        assert get_resp.status_code == 404
        post_resp = client.post(url, json={"upc": context["item_upc"], "quantity": 1})
        assert post_resp.status_code == 404


def test_scan_counts_records_totals(client, app):
    context = _setup_event(app)
    url = f"/events/{context['event_id']}/locations/{context['location_id']}/scan_counts"

    with client:
        login(client, context["email"], "pass")
        get_resp = client.get(url)
        assert get_resp.status_code == 200
        assert b"Scan Inventory Counts" in get_resp.data

        post_resp = client.post(url, json={"upc": context["item_upc"], "quantity": 3})
        assert post_resp.status_code == 200
        payload = post_resp.get_json()
        assert payload["success"] is True
        assert payload["item"]["total"] == pytest.approx(3)

        second_resp = client.post(url, json={"upc": context["item_upc"], "quantity": 2})
        assert second_resp.status_code == 200
        payload = second_resp.get_json()
        assert payload["item"]["total"] == pytest.approx(5)

        refresh = client.get(url, headers={"Accept": "application/json"})
        assert refresh.status_code == 200
        refresh_payload = refresh.get_json()
        assert refresh_payload["success"] is True
        assert refresh_payload["totals"]
        assert refresh_payload["totals"][0]["counted"] == pytest.approx(5)

    with app.app_context():
        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
        ).first()
        assert sheet is not None
        assert sheet.transferred_out == pytest.approx(5)
        assert sheet.closing_count == pytest.approx(5)
