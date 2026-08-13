from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationOperatingDay,
    EventStandSheetItem,
    Item,
    ItemUnit,
    Location,
    LocationCountSubmission,
    LocationCountSubmissionRow,
    LocationStandItem,
    User,
)
from app.services.location_count_submissions import (
    sync_event_location_counts_from_approved_submissions,
)
from tests.utils import login


def _setup_location_count_context(app):
    with app.app_context():
        suffix = uuid4().hex[:8]
        location = Location(name=f"Count Stand {suffix}")
        item = Item(name=f"Count Item {suffix}", base_unit="each")
        db.session.add_all([location, item])
        db.session.flush()

        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                countable=True,
                expected_count=5.0,
            )
        )

        today = date.today()
        event = Event(
            name=f"Count Event {suffix}",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
        )
        db.session.add(event)
        db.session.flush()

        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
        )
        db.session.add(event_location)
        db.session.commit()

        return {
            "location_id": location.id,
            "token": location.count_qr_token,
            "item_id": item.id,
            "event_id": event.id,
            "event_location_id": event_location.id,
            "today": today,
        }


def _create_pending_submission(
    *,
    location_id: int,
    event_location_id: int,
    item_id: int,
    submission_type: str,
    submission_date: date,
    count_value: float,
    submitted_name: str,
) -> int:
    submission = LocationCountSubmission(
        source_location_id=location_id,
        location_id=location_id,
        event_location_id=event_location_id,
        submission_type=submission_type,
        submission_date=submission_date,
        submitted_name=submitted_name,
        status=LocationCountSubmission.STATUS_PENDING,
    )
    db.session.add(submission)
    db.session.flush()
    row = LocationCountSubmissionRow(
        submission_id=submission.id,
        item_id=item_id,
        count_value=count_value,
        submitted_count_value=count_value,
        parse_index=0,
    )
    db.session.add(row)
    db.session.commit()
    return submission.id


def test_public_count_submission_blocks_closing_until_opening_exists(client, app):
    context = _setup_location_count_context(app)
    scan_url = f"/locations/scan/{context['token']}"

    response = client.post(
        scan_url,
        data={
            "submitted_name": "Casey",
            "submission_type": "closing",
            f"count_{context['item_id']}": "4",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Closing counts are locked" in response.data

    with app.app_context():
        assert LocationCountSubmission.query.count() == 0

    response = client.post(
        scan_url,
        data={
            "submitted_name": "Casey",
            "submission_type": "opening",
            f"count_{context['item_id']}": "7",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Opening count submitted for manager review." in response.data

    with app.app_context():
        submissions = LocationCountSubmission.query.all()
        assert len(submissions) == 1
        submission = submissions[0]
        assert submission.event_location_id == context["event_location_id"]
        assert submission.location_id == context["location_id"]
        assert submission.submitted_name == "Casey"
        assert submission.submission_type == LocationCountSubmission.TYPE_OPENING
        assert submission.rows[0].count_value == 7.0

    response = client.get(scan_url)
    assert response.status_code == 200
    assert b'value="closing" selected' in response.data


def test_public_count_submission_uses_default_timezone_for_event_mapping(
    client, app, monkeypatch
):
    from app.utils import timezone as timezone_utils

    local_event_date = date(2026, 5, 21)
    with app.app_context():
        app.config["DEFAULT_TIMEZONE"] = "american/winnipeg"
        suffix = uuid4().hex[:8]
        location = Location(name=f"Late Count Stand {suffix}")
        item = Item(name=f"Late Count Item {suffix}", base_unit="each")
        db.session.add_all([location, item])
        db.session.flush()

        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                countable=True,
                expected_count=0.0,
            )
        )
        event = Event(
            name=f"Late Count Event {suffix}",
            start_date=local_event_date,
            end_date=local_event_date,
        )
        db.session.add(event)
        db.session.flush()

        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
        )
        db.session.add(event_location)
        db.session.commit()

        token = location.count_qr_token
        item_id = item.id
        event_location_id = event_location.id

    monkeypatch.setattr(
        timezone_utils,
        "utc_now",
        lambda: datetime(2026, 5, 22, 0, 54, tzinfo=timezone.utc),
    )

    response = client.post(
        f"/locations/scan/{token}",
        data={
            "submitted_name": "Night Crew",
            "submission_type": "opening",
            f"count_{item_id}": "8",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"Opening count submitted for manager review." in response.data

    with app.app_context():
        submission = LocationCountSubmission.query.one()
        assert submission.submission_date == local_event_date
        assert submission.event_location_id == event_location_id


def test_public_count_submission_renders_mobile_numeric_entry_inputs(client, app):
    context = _setup_location_count_context(app)
    response = client.get(f"/locations/scan/{context['token']}")

    assert response.status_code == 200
    assert b'data-count-form="1"' in response.data
    assert b'data-count-draft-prompt' in response.data
    assert b'data-count-draft-resume="1"' in response.data
    assert b'data-count-draft-discard="1"' in response.data
    assert b'data-count-draft-status' in response.data
    assert b'type="number"' in response.data
    assert b'step="1"' in response.data
    assert b'min="0"' in response.data
    assert b'inputmode="numeric"' in response.data
    assert b'enterkeyhint="next"' in response.data
    assert b'data-count-entry="1"' in response.data
    assert b'data-native-numeric="1"' in response.data
    assert b'data-count-submit="1"' in response.data


def test_public_count_submission_auto_switches_to_inventory_for_inventory_event(
    client, app
):
    today = date.today()
    with app.app_context():
        suffix = uuid4().hex[:8]
        location = Location(name=f"Inventory QR Stand {suffix}")
        item = Item(name=f"Inventory QR Cup {suffix}", base_unit="each", cost=2.5)
        db.session.add_all([location, item])
        db.session.flush()
        unit = ItemUnit(item_id=item.id, name="Case of 12", factor=12)
        db.session.add_all(
            [
                unit,
                LocationStandItem(
                    location_id=location.id,
                    item_id=item.id,
                    countable=True,
                    expected_count=5.0,
                ),
            ]
        )
        event = Event(
            name=f"Inventory QR Event {suffix}",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
            event_type="inventory",
        )
        db.session.add(event)
        db.session.flush()
        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
        )
        db.session.add(event_location)
        db.session.commit()
        token = location.count_qr_token
        item_id = item.id
        unit_id = unit.id
        event_location_id = event_location.id

    response = client.get(f"/locations/scan/{token}")
    assert response.status_code == 200
    assert b"Submit Inventory Count" in response.data
    assert f"inventory_unit_{item_id}_0".encode() in response.data
    assert f"inventory_qty_{item_id}_0".encode() in response.data
    assert b'inputmode="numeric"' in response.data
    assert b'pattern="[0-9]*"' in response.data
    assert b'data-inventory-quantity-entry="1"' in response.data
    assert b'inputmode="decimal"' not in response.data
    assert b"Case of 12" in response.data

    response = client.post(
        f"/locations/scan/{token}",
        data={
            "submitted_name": "Inventory Counter",
            f"inventory_unit_{item_id}_0": str(unit_id),
            f"inventory_qty_{item_id}_0": "2",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Inventory count submitted for manager review." in response.data

    duplicate_response = client.post(
        f"/locations/scan/{token}",
        data={
            "submitted_name": "Inventory Counter",
            f"inventory_unit_{item_id}_0": str(unit_id),
            f"inventory_qty_{item_id}_0": "2",
        },
        follow_redirects=True,
    )
    assert duplicate_response.status_code == 200
    assert b"Inventory count already submitted for manager review." in duplicate_response.data

    with app.app_context():
        submission = LocationCountSubmission.query.one()
        assert submission.submission_type == LocationCountSubmission.TYPE_INVENTORY
        assert submission.event_location_id == event_location_id
        assert submission.rows[0].count_value == 24.0
        assert submission.rows[0].unit_breakdown[0]["quantity"] == 2.0
        assert submission.rows[0].unit_breakdown[0]["base_quantity"] == 24.0
        assert submission.rows[0].expected_count_value == 5.0


def test_removed_inventory_item_hides_from_public_page_but_keeps_pending_submission(
    client, app
):
    today = date.today()
    with app.app_context():
        suffix = uuid4().hex[:8]
        admin = User(
            email=f"inventory-delete-{suffix}@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        location = Location(name=f"Inventory Delete Stand {suffix}")
        item = Item(name=f"Inventory Delete Cup {suffix}", base_unit="each", cost=1.5)
        event = Event(
            name=f"Inventory Delete Event {suffix}",
            start_date=today,
            end_date=today,
            event_type="inventory",
        )
        db.session.add_all([admin, location, item, event])
        db.session.flush()
        unit = ItemUnit(item_id=item.id, name="each", factor=1)
        event_location = EventLocation(event_id=event.id, location_id=location.id)
        db.session.add_all(
            [
                unit,
                event_location,
                LocationStandItem(
                    location_id=location.id,
                    item_id=item.id,
                    countable=True,
                    expected_count=5.0,
                ),
            ]
        )
        db.session.flush()
        operating_day = EventLocationOperatingDay(
            event_location_id=event_location.id,
            operating_date=today,
        )
        db.session.add(operating_day)
        db.session.commit()
        token = location.count_qr_token
        location_id = location.id
        item_id = item.id
        unit_id = unit.id
        event_id = event.id

    inventory_url = (
        f"/locations/scan/{token}/inventory/{event_id}"
        f"?operating_date={today.isoformat()}"
    )

    response = client.get(inventory_url)
    assert response.status_code == 200
    assert f"Inventory Delete Cup {suffix}".encode() in response.data

    response = client.post(
        inventory_url,
        data={
            "submitted_name": "Inventory Counter",
            f"inventory_unit_{item_id}_0": str(unit_id),
            f"inventory_qty_{item_id}_0": "4",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        submission = LocationCountSubmission.query.one()
        assert submission.status == LocationCountSubmission.STATUS_PENDING
        assert submission.rows[0].item_id == item_id
        assert submission.rows[0].count_value == 4.0
        assert submission.rows[0].expected_count_value == 5.0

    with client:
        login(client, f"inventory-delete-{suffix}@example.com", "pass")
        delete_response = client.post(
            f"/locations/{location_id}/items/{item_id}/delete",
            data={"submit": "Delete"},
            follow_redirects=True,
        )
    assert delete_response.status_code == 200
    assert b"Item removed from location" in delete_response.data

    public_after_delete = client.get(inventory_url)
    assert public_after_delete.status_code == 200
    assert f"Inventory Delete Cup {suffix}".encode() not in public_after_delete.data

    with app.app_context():
        submission = LocationCountSubmission.query.one()
        assert submission.status == LocationCountSubmission.STATUS_PENDING
        assert submission.rows[0].item_id == item_id
        record = LocationStandItem.query.filter_by(
            location_id=location_id,
            item_id=item_id,
        ).one()
        assert record.active is False


def test_public_inventory_count_can_add_catalog_item(client, app):
    today = date.today()
    with app.app_context():
        suffix = uuid4().hex[:8]
        location = Location(name=f"Inventory Add Stand {suffix}")
        configured_item = Item(
            name=f"Configured Inventory Item {suffix}",
            base_unit="each",
        )
        missing_item = Item(
            name=f"Missing Inventory Item {suffix}",
            base_unit="each",
            upc=f"{uuid4().int % 10**12:012d}",
        )
        db.session.add_all([location, configured_item, missing_item])
        db.session.flush()
        configured_unit = ItemUnit(
            item_id=configured_item.id,
            name="each",
            factor=1,
        )
        missing_unit = ItemUnit(
            item_id=missing_item.id,
            name="Case of 6",
            factor=6,
        )
        event = Event(
            name=f"Inventory Add Event {suffix}",
            start_date=today,
            end_date=today,
            event_type="inventory",
        )
        event_location = EventLocation(event=event, location=location)
        db.session.add_all(
            [
                configured_unit,
                missing_unit,
                LocationStandItem(
                    location_id=location.id,
                    item_id=configured_item.id,
                    countable=True,
                    expected_count=0.0,
                ),
                event,
                event_location,
            ]
        )
        db.session.commit()
        token = location.count_qr_token
        event_id = event.id
        event_location_id = event_location.id
        configured_item_id = configured_item.id
        missing_item_id = missing_item.id
        missing_unit_id = missing_unit.id
        missing_item_name = missing_item.name
        missing_item_upc = missing_item.upc

    inventory_url = (
        f"/locations/scan/{token}/inventory/{event_id}"
        f"?operating_date={today.isoformat()}"
    )
    response = client.get(inventory_url)
    assert response.status_code == 200
    assert b'data-inventory-filter-input="1"' in response.data
    assert b'data-inventory-filter-clear="1"' in response.data
    assert b'data-inventory-status-filter="counted"' in response.data
    assert b'data-inventory-add-search="1"' in response.data
    assert b"data-inventory-item-search-url=" in response.data
    assert f'data-inventory-item-id="{configured_item_id}"'.encode() in response.data
    assert missing_item_name.encode() not in response.data
    assert missing_item_upc.encode() not in response.data

    search_response = client.get(
        f"/locations/scan/{token}/inventory/{event_id}/items/search",
        query_string={
            "operating_date": today.isoformat(),
            "q": missing_item_name,
        },
    )
    assert search_response.status_code == 200
    search_payload = search_response.get_json()
    assert search_payload["limit"] == 12
    search_items = search_payload["items"]
    assert len(search_items) == 1
    assert search_items[0]["id"] == missing_item_id
    assert search_items[0]["name"] == missing_item_name
    assert search_items[0]["upc"] == missing_item_upc
    assert search_items[0]["unit_options"][1]["value"] == str(missing_unit_id)

    short_search_response = client.get(
        f"/locations/scan/{token}/inventory/{event_id}/items/search",
        query_string={
            "operating_date": today.isoformat(),
            "q": missing_item_name[:1],
        },
    )
    assert short_search_response.status_code == 200
    assert short_search_response.get_json()["items"] == []

    lookup_response = client.get(
        f"/locations/scan/{token}/inventory/{event_id}/items/search",
        query_string={
            "operating_date": today.isoformat(),
            "ids": str(missing_item_id),
        },
    )
    assert lookup_response.status_code == 200
    assert lookup_response.get_json()["items"][0]["id"] == missing_item_id

    response = client.post(
        inventory_url,
        data={
            "submitted_name": "Inventory Counter",
            f"inventory_unit_{missing_item_id}_0": str(missing_unit_id),
            f"inventory_qty_{missing_item_id}_0": "3",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Inventory count submitted for manager review." in response.data

    with app.app_context():
        submission = LocationCountSubmission.query.one()
        assert submission.submission_type == LocationCountSubmission.TYPE_INVENTORY
        assert submission.event_location_id == event_location_id
        assert submission.submission_date == today
        assert len(submission.rows) == 1
        row = submission.rows[0]
        assert row.item_id == missing_item_id
        assert row.count_value == 18.0
        assert row.unit_breakdown[0]["quantity"] == 3.0
        assert row.unit_breakdown[0]["base_quantity"] == 18.0


def test_public_count_submission_prefers_open_regular_event_over_inventory(
    client, app
):
    today = date.today()
    with app.app_context():
        suffix = uuid4().hex[:8]
        location = Location(name=f"Overlap Count Stand {suffix}")
        item = Item(name=f"Overlap Count Cup {suffix}", base_unit="each")
        regular_event = Event(
            name=f"Regular Event {suffix}",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
            event_type="hockey",
        )
        inventory_event = Event(
            name=f"Inventory Event {suffix}",
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=1),
            event_type="inventory",
        )
        db.session.add_all([location, item, regular_event, inventory_event])
        db.session.flush()
        unit = ItemUnit(item_id=item.id, name="each", factor=1)
        regular_event_location = EventLocation(
            event_id=regular_event.id,
            location_id=location.id,
        )
        inventory_event_location = EventLocation(
            event_id=inventory_event.id,
            location_id=location.id,
        )
        db.session.add_all(
            [
                unit,
                LocationStandItem(
                    location_id=location.id,
                    item_id=item.id,
                    countable=True,
                    expected_count=0.0,
                ),
                regular_event_location,
                inventory_event_location,
            ]
        )
        db.session.commit()
        token = location.count_qr_token
        regular_event_id = regular_event.id
        inventory_event_id = inventory_event.id
        item_id = item.id
        unit_id = unit.id

    response = client.get(f"/locations/scan/{token}")
    assert response.status_code == 200
    assert f"Regular Event {suffix}".encode() in response.data
    assert b"Submit Inventory Count" not in response.data
    assert f"inventory_qty_{item_id}_0".encode() not in response.data
    assert f"/locations/scan/{token}/inventory/{inventory_event_id}".encode() in response.data

    response = client.post(
        f"/locations/scan/{token}",
        data={
            "submitted_name": "Event Counter",
            "submission_type": "opening",
            f"count_{item_id}": "3",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    response = client.post(
        f"/locations/scan/{token}",
        data={
            "submitted_name": "Event Counter",
            "submission_type": "closing",
            f"count_{item_id}": "1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Closing count submitted for manager review." in response.data

    inventory_url = (
        f"/locations/scan/{token}/inventory/{inventory_event_id}"
        f"?operating_date={today.isoformat()}"
    )
    response = client.get(inventory_url)
    assert response.status_code == 200
    assert b"Submit Inventory Count" in response.data
    assert f"inventory_qty_{item_id}_0".encode() in response.data

    response = client.post(
        inventory_url,
        data={
            "submitted_name": "Inventory Counter",
            f"inventory_unit_{item_id}_0": str(unit_id),
            f"inventory_qty_{item_id}_0": "8",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Inventory count submitted for manager review." in response.data

    with app.app_context():
        submissions = LocationCountSubmission.query.order_by(
            LocationCountSubmission.id.asc()
        ).all()
        assert [submission.submission_type for submission in submissions] == [
            LocationCountSubmission.TYPE_OPENING,
            LocationCountSubmission.TYPE_CLOSING,
            LocationCountSubmission.TYPE_INVENTORY,
        ]
        assert submissions[0].event_location.event_id == regular_event_id
        assert submissions[1].event_location.event_id == regular_event_id
        assert submissions[2].event_location.event_id == inventory_event_id
        assert submissions[2].submission_date == today
        assert submissions[2].rows[0].count_value == 8.0

        regular_event = db.session.get(Event, regular_event_id)
        assert regular_event.closed is False
        regular_event.closed = True
        db.session.commit()

    response = client.get(f"/locations/scan/{token}")
    assert response.status_code == 200
    assert b"Submit Inventory Count" in response.data


def test_manager_approval_uses_first_opening_day_last_closing_day_and_aggregates_same_day_submissions(
    client, app
):
    context = _setup_location_count_context(app)

    with app.app_context():
        opening_first_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"] - timedelta(days=1),
            count_value=10.0,
            submitted_name="Alex",
        )
        opening_first_same_day_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"] - timedelta(days=1),
            count_value=3.0,
            submitted_name="Jordan",
        )
        opening_later_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"],
            count_value=12.0,
            submitted_name="Bailey",
        )
        closing_last_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_CLOSING,
            submission_date=context["today"] + timedelta(days=1),
            count_value=4.0,
            submitted_name="Casey",
        )
        closing_last_same_day_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_CLOSING,
            submission_date=context["today"] + timedelta(days=1),
            count_value=1.0,
            submitted_name="Morgan",
        )

        opening_first = db.session.get(LocationCountSubmission, opening_first_id)
        opening_first_same_day = db.session.get(
            LocationCountSubmission, opening_first_same_day_id
        )
        opening_later = db.session.get(LocationCountSubmission, opening_later_id)
        closing_last = db.session.get(LocationCountSubmission, closing_last_id)
        closing_last_same_day = db.session.get(
            LocationCountSubmission, closing_last_same_day_id
        )
        opening_first_row_id = opening_first.rows[0].id
        opening_first_same_day_row_id = opening_first_same_day.rows[0].id
        opening_later_row_id = opening_later.rows[0].id
        closing_last_row_id = closing_last.rows[0].id
        closing_last_same_day_row_id = closing_last_same_day.rows[0].id

    with client:
        login(client, "admin@example.com", "adminpass")
        for submission_id, submission_date, submission_type, submitted_name, row_id, value in (
            (
                opening_first_id,
                (context["today"] - timedelta(days=1)).isoformat(),
                "opening",
                "Alex",
                opening_first_row_id,
                "10",
            ),
            (
                opening_first_same_day_id,
                (context["today"] - timedelta(days=1)).isoformat(),
                "opening",
                "Jordan",
                opening_first_same_day_row_id,
                "3",
            ),
            (
                opening_later_id,
                context["today"].isoformat(),
                "opening",
                "Bailey",
                opening_later_row_id,
                "12",
            ),
            (
                closing_last_id,
                (context["today"] + timedelta(days=1)).isoformat(),
                "closing",
                "Casey",
                closing_last_row_id,
                "4",
            ),
            (
                closing_last_same_day_id,
                (context["today"] + timedelta(days=1)).isoformat(),
                "closing",
                "Morgan",
                closing_last_same_day_row_id,
                "1",
            ),
        ):
            response = client.post(
                f"/locations/count-submissions/{submission_id}",
                data={
                    "action": "approve_add",
                    "submitted_name": submitted_name,
                    "submission_type": submission_type,
                    "submission_date": submission_date,
                    "location_id": str(context["location_id"]),
                    "event_location_id": str(context["event_location_id"]),
                    "review_note": "",
                    f"count_{row_id}": value,
                },
                follow_redirects=True,
            )
            assert response.status_code == 200
            assert b"Opening count approved and applied to the stand sheet using add mode." in response.data or b"Closing count approved and applied to the stand sheet using add mode." in response.data

    with app.app_context():
        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
        ).first()
        assert sheet is not None
        assert sheet.opening_count == 13.0
        assert sheet.closing_count == 5.0


def test_event_day_stand_sheet_uses_requested_day_approved_opening(client, app):
    context = _setup_location_count_context(app)
    first_day = context["today"] - timedelta(days=1)
    second_day = context["today"]

    with app.app_context():
        db.session.add_all(
            [
                EventLocationOperatingDay(
                    event_location_id=context["event_location_id"],
                    operating_date=first_day,
                ),
                EventLocationOperatingDay(
                    event_location_id=context["event_location_id"],
                    operating_date=second_day,
                ),
            ]
        )
        db.session.commit()

        first_opening_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=first_day,
            count_value=0.0,
            submitted_name="Day One",
        )
        second_opening_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=second_day,
            count_value=207.0,
            submitted_name="Day Two",
        )

        for submission_id in (first_opening_id, second_opening_id):
            submission = db.session.get(LocationCountSubmission, submission_id)
            submission.status = LocationCountSubmission.STATUS_APPROVED
            submission.approval_mode = LocationCountSubmission.APPROVAL_MODE_ADD
        db.session.commit()

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.get(
            f"/events/{context['event_id']}/stand_sheet/{context['location_id']}"
            f"?operating_date={second_day.isoformat()}"
        )

    assert response.status_code == 200
    assert b'value="207.0000"' in response.data


def test_manager_approval_can_overwrite_same_day_counts(client, app):
    context = _setup_location_count_context(app)

    with app.app_context():
        first_submission_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"],
            count_value=10.0,
            submitted_name="Alex",
        )
        second_submission_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"],
            count_value=5.0,
            submitted_name="Jordan",
        )

        first_submission = db.session.get(LocationCountSubmission, first_submission_id)
        second_submission = db.session.get(LocationCountSubmission, second_submission_id)
        first_row_id = first_submission.rows[0].id
        second_row_id = second_submission.rows[0].id

    with client:
        login(client, "admin@example.com", "adminpass")

        first_response = client.post(
            f"/locations/count-submissions/{first_submission_id}",
            data={
                "action": "approve_add",
                "submitted_name": "Alex",
                "submission_type": "opening",
                "submission_date": context["today"].isoformat(),
                "location_id": str(context["location_id"]),
                "event_location_id": str(context["event_location_id"]),
                "review_note": "",
                f"count_{first_row_id}": "10",
            },
            follow_redirects=True,
        )
        assert first_response.status_code == 200
        assert (
            b"Opening count approved and applied to the stand sheet using add mode."
            in first_response.data
        )

        second_response = client.post(
            f"/locations/count-submissions/{second_submission_id}",
            data={
                "action": "approve_overwrite",
                "submitted_name": "Jordan",
                "submission_type": "opening",
                "submission_date": context["today"].isoformat(),
                "location_id": str(context["location_id"]),
                "event_location_id": str(context["event_location_id"]),
                "review_note": "",
                f"count_{second_row_id}": "5",
            },
            follow_redirects=True,
        )
        assert second_response.status_code == 200
        assert (
            b"Opening count approved and applied to the stand sheet using overwrite mode."
            in second_response.data
        )

    with app.app_context():
        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
        ).first()
        assert sheet is not None
        assert sheet.opening_count == 5.0

        second_submission = db.session.get(LocationCountSubmission, second_submission_id)
        assert (
            second_submission.approval_mode
            == LocationCountSubmission.APPROVAL_MODE_OVERWRITE
        )


def test_inventory_overwrite_replaces_whole_location_count(app):
    context = _setup_location_count_context(app)

    with app.app_context():
        event = db.session.get(Event, context["event_id"])
        event.event_type = "inventory"
        second_item = Item(name=f"Second Inventory Item {uuid4().hex[:8]}", base_unit="each")
        db.session.add(second_item)
        db.session.flush()
        db.session.add(
            LocationStandItem(
                location_id=context["location_id"],
                item_id=second_item.id,
                countable=True,
                expected_count=0.0,
            )
        )
        db.session.commit()

        first_item_id = context["item_id"]
        second_item_id = second_item.id
        first_add_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=first_item_id,
            submission_type=LocationCountSubmission.TYPE_INVENTORY,
            submission_date=context["today"],
            count_value=10.0,
            submitted_name="First Counter",
        )
        second_add_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=second_item_id,
            submission_type=LocationCountSubmission.TYPE_INVENTORY,
            submission_date=context["today"],
            count_value=5.0,
            submitted_name="Second Counter",
        )
        overwrite_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=first_item_id,
            submission_type=LocationCountSubmission.TYPE_INVENTORY,
            submission_date=context["today"],
            count_value=2.0,
            submitted_name="Correction Counter",
        )

        for submission_id, approval_mode in (
            (first_add_id, LocationCountSubmission.APPROVAL_MODE_ADD),
            (second_add_id, LocationCountSubmission.APPROVAL_MODE_ADD),
            (overwrite_id, LocationCountSubmission.APPROVAL_MODE_OVERWRITE),
        ):
            submission = db.session.get(LocationCountSubmission, submission_id)
            submission.status = LocationCountSubmission.STATUS_APPROVED
            submission.approval_mode = approval_mode

        sync_event_location_counts_from_approved_submissions(
            context["event_location_id"]
        )
        db.session.commit()

        first_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=first_item_id,
        ).one()
        second_sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=second_item_id,
        ).one()
        assert first_sheet.closing_count == 2.0
        assert second_sheet.closing_count == 0.0


def test_manager_can_approve_expected_opening_for_event_day(client, app):
    context = _setup_location_count_context(app)

    with app.app_context():
        for operating_date in (
            context["today"] - timedelta(days=1),
            context["today"],
            context["today"] + timedelta(days=1),
        ):
            db.session.add(
                EventLocationOperatingDay(
                    event_location_id=context["event_location_id"],
                    operating_date=operating_date,
                )
            )
        previous_closing = LocationCountSubmission(
            source_location_id=context["location_id"],
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            submission_type=LocationCountSubmission.TYPE_CLOSING,
            submission_date=context["today"] - timedelta(days=1),
            submitted_name="Night Lead",
            status=LocationCountSubmission.STATUS_APPROVED,
            approval_mode=LocationCountSubmission.APPROVAL_MODE_OVERWRITE,
        )
        db.session.add(previous_closing)
        db.session.flush()
        db.session.add(
            LocationCountSubmissionRow(
                submission_id=previous_closing.id,
                item_id=context["item_id"],
                count_value=4.0,
                submitted_count_value=4.0,
                parse_index=0,
            )
        )
        db.session.commit()

        opening_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"],
            count_value=12.0,
            submitted_name="Day Lead",
        )
        opening = db.session.get(LocationCountSubmission, opening_id)
        opening_row_id = opening.rows[0].id

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.get(f"/locations/count-submissions/{opening_id}")
        assert response.status_code == 200
        assert b"Expected opening for" in response.data
        assert b"Approve Expected Opening" in response.data

        response = client.post(
            f"/locations/count-submissions/{opening_id}",
            data={
                "action": "approve_expected_opening",
                "submitted_name": "Day Lead",
                "submission_type": "opening",
                "submission_date": context["today"].isoformat(),
                "location_id": str(context["location_id"]),
                "event_location_id": str(context["event_location_id"]),
                "review_note": "",
                f"count_{opening_row_id}": "12",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert (
            b"Opening count approved and applied to the stand sheet using overwrite mode."
            in response.data
        )

    with app.app_context():
        opening = db.session.get(LocationCountSubmission, opening_id)
        row = opening.rows[0]
        assert opening.applied_count_source == LocationCountSubmission.APPLIED_SOURCE_EXPECTED
        assert row.submitted_count_value == 12.0
        assert row.expected_count_value == 4.0
        assert row.count_value == 4.0

        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
        ).first()
        assert sheet is not None
        assert sheet.opening_count == 4.0


def test_print_count_sign_returns_pdf(client, app):
    context = _setup_location_count_context(app)

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.get(f"/locations/{context['location_id']}/count-sign")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_print_transfer_sign_returns_pdf(client, app):
    context = _setup_location_count_context(app)

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.get(f"/locations/{context['location_id']}/transfer-sign")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")


def test_public_eaten_submission_and_manager_approval_updates_stand_sheet(client, app):
    context = _setup_location_count_context(app)
    scan_url = f"/locations/scan/{context['token']}/eaten"

    response = client.post(
        scan_url,
        data={
            "submitted_name": "Casey",
            f"count_{context['item_id']}": "2",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Eaten items submitted for manager review." in response.data

    with app.app_context():
        submission = LocationCountSubmission.query.order_by(
            LocationCountSubmission.id.desc()
        ).first()
        assert submission is not None
        assert submission.submission_type == LocationCountSubmission.TYPE_EATEN
        row_id = submission.rows[0].id
        submission_id = submission.id

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            f"/locations/count-submissions/{submission_id}",
            data={
                "action": "approve_add",
                "submitted_name": "Casey",
                "submission_type": "eaten",
                "submission_date": context["today"].isoformat(),
                "location_id": str(context["location_id"]),
                "event_location_id": str(context["event_location_id"]),
                "review_note": "",
                f"count_{row_id}": "2",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert (
            b"Eaten items approved and applied to the stand sheet using add mode."
            in response.data
        )

    with app.app_context():
        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
        ).first()
        assert sheet is not None
        assert sheet.eaten == 2.0


def test_spoilage_approval_rolls_up_all_days_and_overwrites_same_day(client, app):
    context = _setup_location_count_context(app)

    with app.app_context():
        first_submission_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_SPOILAGE,
            submission_date=context["today"] - timedelta(days=1),
            count_value=2.0,
            submitted_name="Alex",
        )
        overwrite_submission_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_SPOILAGE,
            submission_date=context["today"] - timedelta(days=1),
            count_value=5.0,
            submitted_name="Jordan",
        )
        later_submission_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_SPOILAGE,
            submission_date=context["today"],
            count_value=3.0,
            submitted_name="Morgan",
        )

        first_row_id = db.session.get(
            LocationCountSubmission, first_submission_id
        ).rows[0].id
        overwrite_row_id = db.session.get(
            LocationCountSubmission, overwrite_submission_id
        ).rows[0].id
        later_row_id = db.session.get(
            LocationCountSubmission, later_submission_id
        ).rows[0].id

    with client:
        login(client, "admin@example.com", "adminpass")
        for submission_id, action, row_id, submission_date, value, submitted_name in (
            (
                first_submission_id,
                "approve_add",
                first_row_id,
                (context["today"] - timedelta(days=1)).isoformat(),
                "2",
                "Alex",
            ),
            (
                overwrite_submission_id,
                "approve_overwrite",
                overwrite_row_id,
                (context["today"] - timedelta(days=1)).isoformat(),
                "5",
                "Jordan",
            ),
            (
                later_submission_id,
                "approve_add",
                later_row_id,
                context["today"].isoformat(),
                "3",
                "Morgan",
            ),
        ):
            response = client.post(
                f"/locations/count-submissions/{submission_id}",
                data={
                    "action": action,
                    "submitted_name": submitted_name,
                    "submission_type": "spoilage",
                    "submission_date": submission_date,
                    "location_id": str(context["location_id"]),
                    "event_location_id": str(context["event_location_id"]),
                    "review_note": "",
                    f"count_{row_id}": value,
                },
                follow_redirects=True,
            )
            assert response.status_code == 200

    with app.app_context():
        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
        ).first()
        assert sheet is not None
        assert sheet.spoiled == 8.0
