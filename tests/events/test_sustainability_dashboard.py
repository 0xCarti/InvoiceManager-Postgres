from datetime import datetime, timedelta

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    Item,
    Location,
    User,
)
from app.routes.event_routes import build_sustainability_report
from tests.utils import login


@pytest.fixture
def sustainability_event(app):
    """Create a user, event, and two locations for sustainability tests."""
    with app.app_context():
        user = User(
            email="sustain@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        event = Event(
            name="Sustainability Summit",
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=1),
            event_type="inventory",
        )
        loc_a = Location(name="Green Stand A")
        loc_b = Location(name="Green Stand B")
        item_a = Item(name="Compostable Plate", base_unit="each", cost=2.5)
        item_b = Item(name="Reusable Cup", base_unit="each", cost=1.0)
        db.session.add_all([user, event, loc_a, loc_b, item_a, item_b])
        db.session.commit()

        el_a = EventLocation(event_id=event.id, location_id=loc_a.id)
        el_b = EventLocation(event_id=event.id, location_id=loc_b.id)
        db.session.add_all([el_a, el_b])
        db.session.commit()

        data = {
            "user_email": user.email,
            "event_id": event.id,
            "event_location_ids": {"A": el_a.id, "B": el_b.id},
            "location_ids": {"A": loc_a.id, "B": loc_b.id},
            "item_ids": {"A": item_a.id, "B": item_b.id},
            "user_id": user.id,
        }

        yield data

        EventStandSheetItem.query.filter(
            EventStandSheetItem.event_location_id.in_(
                data["event_location_ids"].values()
            )
        ).delete(synchronize_session=False)
        for el_id in data["event_location_ids"].values():
            el = db.session.get(EventLocation, el_id)
            if el:
                db.session.delete(el)
        for item_id in data["item_ids"].values():
            item = db.session.get(Item, item_id)
            if item:
                db.session.delete(item)
        for loc_id in data["location_ids"].values():
            loc = db.session.get(Location, loc_id)
            if loc:
                db.session.delete(loc)
        event_obj = db.session.get(Event, data["event_id"])
        if event_obj:
            db.session.delete(event_obj)
        user_obj = db.session.get(User, data["user_id"])
        if user_obj:
            db.session.delete(user_obj)
        db.session.commit()


def test_sustainability_dashboard_aggregates_metrics(client, app, sustainability_event):
    app.config.update({"CARBON_EQ_PER_UNIT": 1.5, "SUSTAINABILITY_WASTE_GOAL": 10})
    payload = sustainability_event

    with app.app_context():
        sheet_a = EventStandSheetItem(
            event_location_id=payload["event_location_ids"]["A"],
            item_id=payload["item_ids"]["A"],
            opening_count=0,
            transferred_in=0,
            transferred_out=0,
            eaten=2,
            spoiled=1,
            closing_count=0,
        )
        sheet_b = EventStandSheetItem(
            event_location_id=payload["event_location_ids"]["B"],
            item_id=payload["item_ids"]["B"],
            opening_count=0,
            transferred_in=0,
            transferred_out=0,
            eaten=0,
            spoiled=4,
            closing_count=0,
        )
        db.session.add_all([sheet_a, sheet_b])
        db.session.commit()

    with app.app_context():
        report = build_sustainability_report(payload["event_id"])

    assert pytest.approx(report["totals"]["waste"], rel=1e-6) == 7.0
    assert pytest.approx(report["totals"]["cost"], rel=1e-6) == 11.5
    assert pytest.approx(report["totals"]["carbon"], rel=1e-6) == 10.5
    assert report["goal"]["target"] == 10
    assert pytest.approx(report["goal"]["remaining"], rel=1e-6) == 3.0
    assert pytest.approx(report["goal"]["progress_pct"], rel=1e-6) == 30.0
    assert report["goal"]["met"] is True
    assert report["location_breakdown"][0]["location"] == "Green Stand B"
    assert report["item_leaderboard"][0]["item"] == "Reusable Cup"

    with client:
        login(client, payload["user_email"], "pass")
        resp = client.get(f"/events/{payload['event_id']}/sustainability")
        assert resp.status_code == 200
        assert b"7.00 units" in resp.data
        assert b"$11.50" in resp.data
        assert b"30.00%" in resp.data

        csv_resp = client.get(
            f"/events/{payload['event_id']}/sustainability/export.csv"
        )
        assert csv_resp.status_code == 200
        assert "Totals,7.00,11.50,10.50" in csv_resp.get_data(as_text=True)


def test_sustainability_dashboard_handles_events_without_data(
    client, app, sustainability_event
):
    payload = sustainability_event

    with client:
        login(client, payload["user_email"], "pass")
        resp = client.get(f"/events/{payload['event_id']}/sustainability")
        assert resp.status_code == 200
        assert b"No stand sheet data yet" in resp.data
        assert b"No stand sheet items recorded for this event." in resp.data

    with app.app_context():
        report = build_sustainability_report(payload["event_id"])
    assert report["totals"]["waste"] == 0
    assert report["totals"]["cost"] == 0
    assert report["totals"]["carbon"] == 0
    assert report["location_breakdown"] == []
    assert report["item_leaderboard"] == []
    assert report["goal"]["target"] is None
    assert report["goal"]["progress_pct"] is None
