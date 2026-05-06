from datetime import date, timedelta
from uuid import uuid4

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    Item,
    Location,
    LocationCountSubmission,
    LocationCountSubmissionRow,
    LocationStandItem,
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


def test_public_count_submission_renders_mobile_numeric_entry_inputs(client, app):
    context = _setup_location_count_context(app)
    response = client.get(f"/locations/scan/{context['token']}")

    assert response.status_code == 200
    assert b'inputmode="numeric"' in response.data
    assert b'pattern="[0-9]*"' in response.data
    assert b'enterkeyhint="next"' in response.data
    assert b'data-count-entry="1"' in response.data
    assert b'data-count-submit="1"' in response.data


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
            submission_date=context["today"] - timedelta(days=2),
            count_value=10.0,
            submitted_name="Alex",
        )
        opening_first_same_day_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"] - timedelta(days=2),
            count_value=3.0,
            submitted_name="Jordan",
        )
        opening_later_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submission_date=context["today"] - timedelta(days=1),
            count_value=12.0,
            submitted_name="Bailey",
        )
        closing_last_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_CLOSING,
            submission_date=context["today"],
            count_value=4.0,
            submitted_name="Casey",
        )
        closing_last_same_day_id = _create_pending_submission(
            location_id=context["location_id"],
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
            submission_type=LocationCountSubmission.TYPE_CLOSING,
            submission_date=context["today"],
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
                (context["today"] - timedelta(days=2)).isoformat(),
                "opening",
                "Alex",
                opening_first_row_id,
                "10",
            ),
            (
                opening_first_same_day_id,
                (context["today"] - timedelta(days=2)).isoformat(),
                "opening",
                "Jordan",
                opening_first_same_day_row_id,
                "3",
            ),
            (
                opening_later_id,
                (context["today"] - timedelta(days=1)).isoformat(),
                "opening",
                "Bailey",
                opening_later_row_id,
                "12",
            ),
            (
                closing_last_id,
                context["today"].isoformat(),
                "closing",
                "Casey",
                closing_last_row_id,
                "4",
            ),
            (
                closing_last_same_day_id,
                context["today"].isoformat(),
                "closing",
                "Morgan",
                closing_last_same_day_row_id,
                "1",
            ),
        ):
            response = client.post(
                f"/locations/count-submissions/{submission_id}",
                data={
                    "action": "approve",
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
            assert b"Count submission approved and applied to the stand sheet." in response.data

    with app.app_context():
        sheet = EventStandSheetItem.query.filter_by(
            event_location_id=context["event_location_id"],
            item_id=context["item_id"],
        ).first()
        assert sheet is not None
        assert sheet.opening_count == 13.0
        assert sheet.closing_count == 5.0


def test_print_count_sign_returns_pdf(client, app):
    context = _setup_location_count_context(app)

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.get(f"/locations/{context['location_id']}/count-sign")

    assert response.status_code == 200
    assert response.mimetype == "application/pdf"
    assert response.data.startswith(b"%PDF")
