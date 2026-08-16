from __future__ import annotations

import json
import re
from datetime import date, datetime
from html import unescape

from werkzeug.datastructures import MultiDict
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationOperatingDay,
    Item,
    Location,
    LocationCountSubmission,
    PosSalesImport,
    PosSalesImportLocation,
    User,
)
from tests.permission_helpers import grant_event_permissions, grant_permissions
from tests.utils import extract_csrf_token, login


def _seed_event_user(
    app,
    *,
    email: str,
    with_item: bool = False,
    event_type: str = "inventory",
):
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
            event_type=event_type,
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


def _seed_pending_event_submissions(app, seeded: dict) -> tuple[int, int, int, int]:
    submission_date = date(2026, 4, 1)
    with app.app_context():
        decoy_submission = LocationCountSubmission(
            source_location_id=seeded["location_id"],
            location_id=seeded["location_id"],
            event_location_id=seeded["event_location_id"],
            submission_type=LocationCountSubmission.TYPE_EATEN,
            submitted_name="Decoy Lead",
            submission_date=submission_date,
            status=LocationCountSubmission.STATUS_REJECTED,
        )
        opening_submission = LocationCountSubmission(
            source_location_id=seeded["location_id"],
            location_id=seeded["location_id"],
            event_location_id=seeded["event_location_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submitted_name="Opening Lead",
            submission_date=submission_date,
            status=LocationCountSubmission.STATUS_PENDING,
        )
        closing_submission = LocationCountSubmission(
            source_location_id=seeded["location_id"],
            location_id=seeded["location_id"],
            event_location_id=seeded["event_location_id"],
            submission_type=LocationCountSubmission.TYPE_CLOSING,
            submitted_name="Closing Lead",
            submission_date=submission_date,
            status=LocationCountSubmission.STATUS_PENDING,
        )
        db.session.add_all(
            [
                decoy_submission,
                opening_submission,
                closing_submission,
            ]
        )
        db.session.flush()
        opening_submission_id = opening_submission.id
        closing_submission_id = closing_submission.id
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="<pending-event-navigation>",
            attachment_filename="pending-sales.xls",
            attachment_sha256="e" * 64,
            sales_date=submission_date,
            status=PosSalesImport.STATUS_PENDING,
        )
        db.session.add(sales_import)
        db.session.flush()
        db.session.add(
            PosSalesImportLocation(
                import_id=sales_import.id,
                source_location_name="Unmapped Source",
                normalized_location_name="unmapped source",
                parse_index=0,
            )
        )
        target_import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Main Bar",
            normalized_location_name="main bar",
            location_id=seeded["location_id"],
            # Fresh imports are resolved from location + sales date before their
            # event_location_id has been persisted by the review workflow.
            event_location_id=None,
            parse_index=1,
        )
        db.session.add(target_import_location)
        db.session.flush()
        if target_import_location.id == seeded["location_id"]:
            target_import_location.location_id = None
            target_import_location.source_location_name = "Second Unmapped Source"
            target_import_location.normalized_location_name = "second unmapped source"
            target_import_location = PosSalesImportLocation(
                import_id=sales_import.id,
                source_location_name="Main Bar",
                normalized_location_name="main bar",
                location_id=seeded["location_id"],
                event_location_id=None,
                parse_index=2,
            )
            db.session.add(target_import_location)
            db.session.flush()
        db.session.commit()
        assert target_import_location.id != seeded["location_id"]
        return (
            opening_submission_id,
            closing_submission_id,
            sales_import.id,
            target_import_location.id,
        )


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
    assert 'data-event-document-form="1"' not in body
    assert "Use current filename" not in body
    assert "Stand Sheet" not in body
    assert "Count Sheet" not in body
    assert "Scan Counts" not in body
    assert "Enter Sales" not in body
    assert "Cumulative Sales" not in body
    assert "Main Bar" in body
    assert f'href="/locations/{seeded["location_id"]}"' not in body
    assert f"/events/{seeded['event_id']}/locations/{seeded['event_location_id']}/confirm" not in body
    assert "/undo_confirm_location" not in body


def test_event_location_name_links_to_location_detail_when_permitted(client, app):
    seeded = _seed_event_user(app, email="event-location-link@example.com")

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.view",
            "locations.view",
            group_name=f"Event Location Link {user.email}",
            description="View event and location details.",
        )

    with client:
        login(client, seeded["email"], "pass")
        response = client.get(f"/events/{seeded['event_id']}")
        body = response.data.decode()

    assert response.status_code == 200
    assert (
        f'<a href="/locations/{seeded["location_id"]}">Main Bar</a>' in body
    )


def test_event_pending_badges_link_to_their_review_pages(client, app):
    seeded = _seed_event_user(
        app,
        email="event-pending-links@example.com",
        event_type="other",
    )
    (
        opening_submission_id,
        closing_submission_id,
        sales_import_id,
        import_location_id,
    ) = _seed_pending_event_submissions(app, seeded)

    with app.app_context():
        original_opening = db.session.get(
            LocationCountSubmission, opening_submission_id
        )
        original_opening.submitted_at = datetime(2026, 4, 1, 9, 0)
        latest_pending_opening = LocationCountSubmission(
            source_location_id=seeded["location_id"],
            location_id=seeded["location_id"],
            event_location_id=seeded["event_location_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submitted_name="Latest Pending Opening Lead",
            submission_date=date(2026, 4, 1),
            status=LocationCountSubmission.STATUS_PENDING,
            submitted_at=datetime(2026, 4, 1, 10, 0),
        )
        newer_approved_opening = LocationCountSubmission(
            source_location_id=seeded["location_id"],
            location_id=seeded["location_id"],
            event_location_id=seeded["event_location_id"],
            submission_type=LocationCountSubmission.TYPE_OPENING,
            submitted_name="Approved Opening Lead",
            submission_date=date(2026, 4, 1),
            status=LocationCountSubmission.STATUS_APPROVED,
            submitted_at=datetime(2026, 4, 1, 11, 0),
        )
        db.session.add_all([latest_pending_opening, newer_approved_opening])
        db.session.flush()
        latest_pending_opening_id = latest_pending_opening.id
        newer_approved_opening_id = newer_approved_opening.id
        db.session.commit()

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.view",
            "events.manage_locations",
            "sales_imports.view",
            group_name=f"Event Pending Links {user.email}",
            description="Review pending event counts and sales imports.",
        )

    with client:
        login(client, seeded["email"], "pass")
        response = client.get(f"/events/{seeded['event_id']}")
        body = unescape(response.data.decode())

    assert response.status_code == 200
    assert (
        f'href="/locations/count-submissions/{latest_pending_opening_id}"' in body
    )
    assert f'href="/locations/count-submissions/{closing_submission_id}"' in body
    assert f'href="/locations/count-submissions/{opening_submission_id}"' not in body
    assert f'href="/locations/count-submissions/{newer_approved_opening_id}"' not in body
    assert "status=pending&submission_type=opening" not in body
    assert "status=pending&submission_type=closing" not in body
    assert (
        f'href="/controlpanel/sales-imports/{sales_import_id}'
        f'?location_id={import_location_id}&return_event_id={seeded["event_id"]}"' in body
    )

    with client:
        detail_response = client.get(
            f"/locations/count-submissions/{latest_pending_opening_id}"
        )
        detail_body = detail_response.get_data(as_text=True)

    assert detail_response.status_code == 200
    assert (
        f"Opening Count Submission #{latest_pending_opening_id}" in detail_body
    )
    assert "Latest Pending Opening Lead" in detail_body


def test_event_pending_badges_stay_plain_without_review_permissions(client, app):
    seeded = _seed_event_user(
        app,
        email="event-pending-view-only@example.com",
        event_type="other",
    )
    (
        opening_submission_id,
        closing_submission_id,
        sales_import_id,
        _,
    ) = _seed_pending_event_submissions(app, seeded)

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.view",
            group_name=f"Event Pending View Only {user.email}",
            description="View event pending states without review access.",
        )

    with client:
        login(client, seeded["email"], "pass")
        response = client.get(f"/events/{seeded['event_id']}")
        body = unescape(response.data.decode())
        direct_review_response = client.get(
            f"/locations/count-submissions/{opening_submission_id}"
        )

    assert response.status_code == 200
    assert body.count("text-bg-warning") >= 3
    assert "status=pending&submission_type=opening" not in body
    assert "status=pending&submission_type=closing" not in body
    assert f"/locations/count-submissions/{opening_submission_id}" not in body
    assert f"/locations/count-submissions/{closing_submission_id}" not in body
    assert f"/controlpanel/sales-imports/{sales_import_id}" not in body
    assert direct_review_response.status_code == 403


def test_inventory_event_detail_hides_regular_location_workflow_controls(
    client, app
):
    event_date = date(2026, 4, 3)
    with app.app_context():
        user = User(
            email="inventory-detail-controls@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Inventory Detail Stand")
        event = Event(
            name="Inventory Detail Event",
            start_date=event_date,
            end_date=event_date,
            event_type="inventory",
        )
        db.session.add_all([user, location, event])
        db.session.flush()
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.flush()
        db.session.add(
            EventLocationOperatingDay(
                event_location_id=event_location.id,
                operating_date=event_date,
            )
        )
        db.session.commit()
        grant_event_permissions(user)
        event_id = event.id
        event_location_id = event_location.id
        location_id = location.id
        count_qr_token = location.count_qr_token

    with client:
        login(client, "inventory-detail-controls@example.com", "pass")
        response = client.get(f"/events/{event_id}")
        cumulative_response = client.get(f"/events/{event_id}/sales/cumulative")
        body = response.data.decode()

    assert response.status_code == 200
    assert cumulative_response.status_code == 404
    assert f"/events/{event_id}/sales/upload" not in body
    assert f"/events/{event_id}/locations/{event_location_id}/sales/add" not in body
    assert f"/events/{event_id}/stand_sheet/{location_id}" not in body
    assert "Opening</th>" not in body
    assert "Closing</th>" not in body
    assert "Sales</th>" not in body
    assert "Estimated Sales" not in body
    assert "Actual Confirmed Sales" not in body
    assert "Physical vs Terminal Variance" not in body
    assert "Terminal sales assignment conflict" not in body
    assert ">Counts</a>" not in body
    assert ">Sales</a>" not in body
    assert f"/events/{event_id}/count_sheet/{location_id}" in body
    assert f"/events/{event_id}/locations/{location_id}/scan_counts" not in body
    assert "Scan Counts" not in body
    assert f"/locations/scan/{count_qr_token}/inventory/{event_id}" in body
    assert (
        f"/locations/count-submissions?event_location_id={event_location_id}"
        in body
    )
    assert "submission_type=inventory" in body
    assert "status=pending" in body


def test_closed_inventory_event_detail_shows_only_inventory_reports(client, app):
    event_date = date(2026, 4, 4)
    with app.app_context():
        user = User(
            email="inventory-reports-only@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Inventory Reports Stand")
        event = Event(
            name="Inventory Reports Event",
            start_date=event_date,
            end_date=event_date,
            event_type="inventory",
            closed=True,
        )
        db.session.add_all([user, location, event])
        db.session.flush()
        event_location = EventLocation(event=event, location=location, confirmed=True)
        db.session.add(event_location)
        db.session.commit()
        grant_event_permissions(user)
        event_id = event.id

    with client:
        login(client, "inventory-reports-only@example.com", "pass")
        response = client.get(f"/events/{event_id}")
        body = response.data.decode()

    assert response.status_code == 200
    assert "Closed Event Report" not in body
    assert "Count Sheet Report" in body
    assert "Summary Source 18" in body
    assert "Inventory Comparison" in body


def test_report_only_user_cannot_mutate_stand_sheet(client, app):
    seeded = _seed_event_user(
        app,
        email="stand-sheet-report-only@example.com",
        event_type="other",
    )

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.reports",
            group_name=f"Event Reports Only {user.email}",
            description="Can view event reports without changing count data.",
        )

    path = f"/events/{seeded['event_id']}/stand_sheet/{seeded['location_id']}"
    with client:
        login(client, seeded["email"], "pass")
        page = client.get(path)
        body = page.data.decode()
        token = extract_csrf_token(page)
        response = client.post(
            path,
            data={"csrf_token": token, "notes": "unauthorized change"},
        )

    assert page.status_code == 200
    assert "This stand sheet is read only for your account." in body
    assert ">Save</button>" not in body
    assert response.status_code == 403
    with app.app_context():
        event_location = db.session.get(
            EventLocation, seeded["event_location_id"]
        )
        assert event_location.notes is None


def test_daily_stand_sheet_print_requires_report_access(client, app):
    seeded = _seed_event_user(
        app,
        email="daily-sheet-permissions@example.com",
        event_type="other",
    )
    operating_date = date(2026, 4, 1)
    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        db.session.add(
            EventLocationOperatingDay(
                event_location_id=seeded["event_location_id"],
                operating_date=operating_date,
            )
        )
        db.session.commit()
        grant_permissions(
            user,
            "events.view",
            group_name=f"Daily Sheet View Only {user.email}",
            description="Can view events without report access.",
        )

    path = (
        f"/events/{seeded['event_id']}/stand_sheet/{seeded['location_id']}/print"
        f"?operating_date={operating_date.isoformat()}"
    )
    cumulative_path = f"/events/{seeded['event_id']}/sales/cumulative"
    with client:
        login(client, seeded["email"], "pass")
        event_page = client.get(f"/events/{seeded['event_id']}")
        denied = client.get(path)
        cumulative_denied = client.get(cumulative_path)

    assert event_page.status_code == 200
    assert path not in unescape(event_page.data.decode())
    assert "Print Sheet" not in event_page.data.decode()
    assert denied.status_code == 403
    assert "Cumulative Sales" not in event_page.data.decode()
    assert cumulative_denied.status_code == 403

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.reports",
            group_name=f"Daily Sheet Reports {user.email}",
            description="Can view printable daily stand sheets.",
        )

    with client:
        client.post("/auth/logout")
        login(client, seeded["email"], "pass")
        allowed_event_page = client.get(f"/events/{seeded['event_id']}")
        allowed = client.get(path)
        cumulative_allowed = client.get(cumulative_path)

    assert allowed_event_page.status_code == 200
    assert path in unescape(allowed_event_page.data.decode())
    assert "Print Sheet" in allowed_event_page.data.decode()
    assert allowed.status_code == 200
    assert "Cumulative Sales" in allowed_event_page.data.decode()
    assert cumulative_allowed.status_code == 200


def test_report_only_user_cannot_submit_inventory_count_sheet(client, app):
    seeded = _seed_event_user(
        app,
        email="count-sheet-report-only@example.com",
        with_item=True,
    )

    with app.app_context():
        user = User.query.filter_by(email=seeded["email"]).one()
        grant_permissions(
            user,
            "events.reports",
            group_name=f"Inventory Reports Only {user.email}",
            description="Can view inventory reports without submitting counts.",
        )

    path = f"/events/{seeded['event_id']}/count_sheet/{seeded['location_id']}"
    with client:
        login(client, seeded["email"], "pass")
        page = client.get(path)
        body = page.data.decode()
        token = extract_csrf_token(page)
        response = client.post(
            path,
            data={
                "csrf_token": token,
                "submitted_name": "Unauthorized Counter",
            },
        )

    assert page.status_code == 200
    assert "This count sheet is read only for your account." in body
    assert "Submit For Review" not in body
    assert 'data-inventory-add-panel="1"' not in body
    assert 'data-inventory-add-row=' not in body
    assert 'data-inventory-remove-row="1"' not in body
    assert response.status_code == 403
    with app.app_context():
        assert LocationCountSubmission.query.count() == 0


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
