from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationOperatingDay,
    Item,
    ItemUnit,
    Location,
    LocationCountSubmission,
    LocationCountSubmissionRow,
    LocationStandItem,
    Menu,
    Product,
    ProductRecipeItem,
    Permission,
    PermissionGroup,
    User,
)
from app.services.location_count_submissions import (
    sync_event_location_counts_from_approved_submissions,
)
from app.utils.menu_assignments import set_location_menu
from tests.utils import login


def _approve_inventory_submission(app, *, confirmed: bool = True) -> None:
    with app.app_context():
        submission = LocationCountSubmission.query.order_by(
            LocationCountSubmission.id.desc()
        ).first()
        assert submission is not None
        submission.status = LocationCountSubmission.STATUS_APPROVED
        submission.approval_mode = LocationCountSubmission.APPROVAL_MODE_ADD
        sync_event_location_counts_from_approved_submissions(
            submission.event_location_id
        )
        if confirmed and submission.event_location is not None:
            submission.event_location.confirmed = True
        db.session.commit()


def _grant_event_permissions(user: User) -> None:
    codes = [
        "events.view",
        "events.create",
        "events.edit",
        "events.delete",
        "events.manage_locations",
        "events.manage_sales",
        "events.confirm_locations",
        "events.close",
        "events.reports",
    ]
    group = PermissionGroup(
        name=f"Event Test Group {user.email}",
        description="Test permissions for event workflows.",
    )
    group.permissions = Permission.query.filter(Permission.code.in_(codes)).all()
    db.session.add(group)
    db.session.flush()
    user.permission_groups.append(group)
    user.invalidate_permission_cache()
    db.session.commit()


def test_count_sheet_shows_location_items_without_products(client, app):
    with app.app_context():
        user = User(
            email="sheet@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Warehouse")
        item = Item(name="Widget", base_unit="each")
        db.session.add_all([user, loc, item])
        db.session.commit()
        _grant_event_permissions(user)
        iu = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        lsi = LocationStandItem(
            location_id=loc.id, item_id=item.id, expected_count=5
        )
        db.session.add_all([iu, lsi])
        db.session.commit()
        loc_id = loc.id
        item_name = item.name

    with client:
        login(client, "sheet@example.com", "pass")
        client.post(
            "/events/create",
            data={
                "name": "InvEvent",
                "start_date": "2023-01-01",
                "end_date": "2023-01-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="InvEvent").first()
        eid = ev.id

    with client:
        login(client, "sheet@example.com", "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        resp = client.get(f"/events/{eid}/count_sheet/{loc_id}")
        assert resp.status_code == 200
        assert item_name.encode() in resp.data


def test_inventory_event_redirects_stand_sheets_to_count_sheets(client, app):
    with app.app_context():
        user = User(
            email="inventory-routes@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Inventory Route Stand")
        old_item = Item(name="Old Menu Cup", base_unit="each")
        old_unit = ItemUnit(
            item=old_item,
            name="Case of 12",
            factor=12,
            receiving_default=True,
            transfer_default=True,
        )
        old_product = Product(name="Old Coffee", price=2.0, cost=1.0)
        current_item = Item(name="Current Menu Lid", base_unit="each")
        current_unit = ItemUnit(
            item=current_item,
            name="Sleeve of 50",
            factor=50,
            receiving_default=True,
            transfer_default=True,
        )
        current_product = Product(name="Current Coffee", price=3.0, cost=1.5)
        old_menu = Menu(name="Old Inventory Menu")
        current_menu = Menu(name="Current Inventory Menu")
        old_menu.products.append(old_product)
        current_menu.products.append(current_product)
        db.session.add_all(
            [
                user,
                loc,
                old_item,
                old_unit,
                old_product,
                current_item,
                current_unit,
                current_product,
                old_menu,
                current_menu,
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                ProductRecipeItem(
                    product_id=old_product.id,
                    item_id=old_item.id,
                    unit_id=old_unit.id,
                    quantity=1,
                    countable=False,
                ),
                ProductRecipeItem(
                    product_id=current_product.id,
                    item_id=current_item.id,
                    unit_id=current_unit.id,
                    quantity=1,
                    countable=False,
                ),
            ]
        )
        set_location_menu(loc, old_menu)
        db.session.flush()
        set_location_menu(loc, current_menu)
        db.session.flush()
        old_record = LocationStandItem.query.filter_by(
            location_id=loc.id,
            item_id=old_item.id,
        ).one()
        current_record = LocationStandItem.query.filter_by(
            location_id=loc.id,
            item_id=current_item.id,
        ).one()
        assert old_record.countable is False
        assert current_record.countable is False
        event = Event(
            name="Inventory Route Event",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 2),
            event_type="inventory",
        )
        event_location = EventLocation(event=event, location=loc)
        db.session.add_all([event, event_location])
        db.session.commit()
        _grant_event_permissions(user)
        event_id = event.id
        location_id = loc.id

    with client:
        login(client, "inventory-routes@example.com", "pass")
        stand_response = client.get(
            f"/events/{event_id}/stand_sheet/{location_id}",
            follow_redirects=False,
        )
        assert stand_response.status_code == 302
        assert stand_response.headers["Location"].endswith(
            f"/events/{event_id}/count_sheet/{location_id}"
        )

        bulk_stand_response = client.get(
            f"/events/{event_id}/stand_sheets",
            follow_redirects=False,
        )
        assert bulk_stand_response.status_code == 302
        assert bulk_stand_response.headers["Location"].endswith(
            f"/events/{event_id}/count_sheets"
        )

        bulk_count_response = client.get(f"/events/{event_id}/count_sheets")
        assert bulk_count_response.status_code == 200
        assert b"Count Sheet Report - Inventory Route Event" in bulk_count_response.data
        assert b"Export CSV" in bulk_count_response.data
        assert b"Receiving Count" in bulk_count_response.data
        assert b"Receiving Unit" in bulk_count_response.data
        assert b"Transfer Count" in bulk_count_response.data
        assert b"Transfer Unit" in bulk_count_response.data
        assert b"Base Count" in bulk_count_response.data
        assert b"Base Unit" in bulk_count_response.data
        assert b"Old Menu Cup" in bulk_count_response.data
        assert b"Current Menu Lid" in bulk_count_response.data
        assert b"Case of 12" in bulk_count_response.data
        assert b"Sleeve of 50" in bulk_count_response.data
        assert bulk_count_response.data.count(b"Old Menu Cup") == 1

        bulk_count_csv_response = client.get(f"/events/{event_id}/count_sheets.csv")
        assert bulk_count_csv_response.status_code == 200
        assert bulk_count_csv_response.headers["Content-Type"].startswith("text/csv")
        csv_body = bulk_count_csv_response.data.decode()
        assert (
            "Location,Item Name,Receiving Count,Receiving Unit,"
            "Transfer Count,Transfer Unit,Base Count,Base Unit"
        ) in csv_body
        assert "Inventory Route Stand,Old Menu Cup,,Case of 12 (12 Each),,Case of 12 (12 Each),,Each" in csv_body
        assert "Inventory Route Stand,Current Menu Lid,,Sleeve of 50 (50 Each),,Sleeve of 50 (50 Each),,Each" in csv_body


def test_inventory_count_sheet_skips_removed_previous_menu_items(client, app):
    with app.app_context():
        user = User(
            email="inventory-removed-previous@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Removed Previous Menu Stand")
        old_item = Item(name="Removed Old Sauce", base_unit="each")
        old_unit = ItemUnit(item=old_item, name="Jug", factor=1)
        old_product = Product(name="Old Sauce Product", price=2.0, cost=1.0)
        current_item = Item(name="Current Sauce Cup", base_unit="each")
        current_unit = ItemUnit(item=current_item, name="Sleeve", factor=1)
        current_product = Product(name="Current Sauce Product", price=3.0, cost=1.5)
        old_menu = Menu(name="Removed Previous Menu")
        current_menu = Menu(name="Removed Current Menu")
        old_menu.products.append(old_product)
        current_menu.products.append(current_product)
        db.session.add_all(
            [
                user,
                loc,
                old_item,
                old_unit,
                old_product,
                current_item,
                current_unit,
                current_product,
                old_menu,
                current_menu,
            ]
        )
        db.session.flush()
        db.session.add_all(
            [
                ProductRecipeItem(
                    product_id=old_product.id,
                    item_id=old_item.id,
                    unit_id=old_unit.id,
                    quantity=1,
                    countable=False,
                ),
                ProductRecipeItem(
                    product_id=current_product.id,
                    item_id=current_item.id,
                    unit_id=current_unit.id,
                    quantity=1,
                    countable=False,
                ),
            ]
        )
        set_location_menu(loc, old_menu)
        db.session.flush()
        set_location_menu(loc, current_menu)
        db.session.flush()
        old_record = LocationStandItem.query.filter_by(
            location_id=loc.id,
            item_id=old_item.id,
        ).one()
        old_record.active = False
        event = Event(
            name="Removed Previous Inventory",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 1, 2),
            event_type="inventory",
        )
        event_location = EventLocation(event=event, location=loc)
        db.session.add_all([event, event_location])
        db.session.commit()
        _grant_event_permissions(user)
        event_id = event.id
        location_id = loc.id

    with client:
        login(client, "inventory-removed-previous@example.com", "pass")
        response = client.get(f"/events/{event_id}/count_sheet/{location_id}")

    assert response.status_code == 200
    assert b"Current Sauce Cup" in response.data
    assert b"Removed Old Sauce" not in response.data


def test_inventory_count_sheet_allows_location_in_open_regular_event(client, app):
    today = date.today()
    with app.app_context():
        user = User(
            email="inventory-overlap@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Inventory Overlap Stand")
        item = Item(name="Inventory Overlap Cup", base_unit="each")
        regular_event = Event(
            name="Open Hockey Event",
            start_date=today,
            end_date=today,
            event_type="hockey",
        )
        inventory_event = Event(
            name="Monthly Inventory",
            start_date=today,
            end_date=today,
            event_type="inventory",
        )
        db.session.add_all([user, loc, item, regular_event, inventory_event])
        db.session.flush()
        db.session.add_all(
            [
                ItemUnit(item_id=item.id, name="each", factor=1),
                LocationStandItem(
                    location_id=loc.id,
                    item_id=item.id,
                    expected_count=5,
                ),
                EventLocation(
                    event_id=regular_event.id,
                    location_id=loc.id,
                ),
                EventLocation(
                    event_id=inventory_event.id,
                    location_id=loc.id,
                ),
            ]
        )
        db.session.commit()
        _grant_event_permissions(user)
        inventory_event_id = inventory_event.id
        location_id = loc.id

    with client:
        login(client, "inventory-overlap@example.com", "pass")
        response = client.get(
            f"/events/{inventory_event_id}/count_sheet/{location_id}"
        )
        assert response.status_code == 200
        assert b"Inventory Count Sheet" in response.data
        assert b"still assigned to open event" not in response.data


def test_inventory_count_sheet_can_search_and_submit_added_item(client, app):
    with app.app_context():
        user = User(
            email="inventory-countsheet-add@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Inventory Countsheet Add Stand")
        configured_item = Item(name="Configured Countsheet Cup", base_unit="each")
        missing_item = Item(name="Missing Countsheet Lid", base_unit="each")
        event = Event(
            name="Monthly Countsheet Add Inventory",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 1),
            event_type="inventory",
        )
        db.session.add_all([user, loc, configured_item, missing_item, event])
        db.session.flush()
        configured_unit = ItemUnit(
            item_id=configured_item.id,
            name="each",
            factor=1,
        )
        missing_unit = ItemUnit(
            item_id=missing_item.id,
            name="each",
            factor=1,
        )
        db.session.add_all(
            [
                configured_unit,
                missing_unit,
                LocationStandItem(
                    location_id=loc.id,
                    item_id=configured_item.id,
                    expected_count=4,
                ),
                EventLocation(
                    event_id=event.id,
                    location_id=loc.id,
                ),
            ]
        )
        db.session.commit()
        _grant_event_permissions(user)
        event_id = event.id
        location_id = loc.id
        configured_item_id = configured_item.id
        missing_item_id = missing_item.id
        missing_unit_id = missing_unit.id

    with client:
        login(client, "inventory-countsheet-add@example.com", "pass")
        response = client.get(f"/events/{event_id}/count_sheet/{location_id}")

        assert response.status_code == 200
        assert b'data-inventory-filter-input="1"' in response.data
        assert b'data-inventory-filter-clear="1"' in response.data
        assert b'data-inventory-add-search="1"' in response.data
        assert b"data-inventory-item-search-url=" in response.data
        assert b'inputmode="numeric"' in response.data
        assert b'pattern="[0-9]*"' in response.data
        assert b'data-inventory-quantity-entry="1"' in response.data
        assert b'data-native-numeric="1"' in response.data
        assert b'inputmode="decimal"' not in response.data
        assert f'data-inventory-item-id="{configured_item_id}"'.encode() in response.data
        assert b"Missing Countsheet Lid" not in response.data

        short_search = client.get(
            f"/events/{event_id}/count_sheet/{location_id}/items/search?q=M"
        )
        assert short_search.status_code == 200
        assert short_search.get_json()["items"] == []
        assert "Type at least 2 characters" in short_search.get_json()["message"]

        search_response = client.get(
            f"/events/{event_id}/count_sheet/{location_id}/items/search?q=Missing"
        )
        assert search_response.status_code == 200
        search_payload = search_response.get_json()
        assert [item["id"] for item in search_payload["items"]] == [missing_item_id]

        post_response = client.post(
            f"/events/{event_id}/count_sheet/{location_id}",
            data={
                "submitted_name": "Inventory Counter",
                f"inventory_unit_{missing_item_id}_0": str(missing_unit_id),
                f"inventory_qty_{missing_item_id}_0": "3",
            },
            follow_redirects=True,
        )
        assert post_response.status_code == 200
        assert b"Inventory count submitted for manager review." in post_response.data

    with app.app_context():
        submission = LocationCountSubmission.query.order_by(
            LocationCountSubmission.id.desc()
        ).first()
        assert submission is not None
        assert submission.submission_type == LocationCountSubmission.TYPE_INVENTORY
        assert submission.rows[0].item_id == missing_item_id
        assert submission.rows[0].count_value == 3


def test_inventory_count_sheet_shows_approved_counts_for_selected_day(client, app):
    first_day = date(2026, 6, 1)
    second_day = date(2026, 6, 2)
    with app.app_context():
        user = User(
            email="inventory-approved-display@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Approved Display Stand")
        item = Item(name="Approved Display Cup", base_unit="each")
        added_item = Item(name="Approved Added Napkin", base_unit="each")
        event = Event(
            name="Approved Display Inventory",
            start_date=first_day,
            end_date=second_day,
            event_type="inventory",
        )
        db.session.add_all([user, loc, item, added_item, event])
        db.session.flush()
        unit = ItemUnit(item_id=item.id, name="each", factor=1)
        added_unit = ItemUnit(item_id=added_item.id, name="each", factor=1)
        event_location = EventLocation(event_id=event.id, location_id=loc.id)
        db.session.add_all(
            [
                unit,
                added_unit,
                LocationStandItem(
                    location_id=loc.id,
                    item_id=item.id,
                    expected_count=5,
                ),
                event_location,
            ]
        )
        db.session.flush()
        first_operating_day = EventLocationOperatingDay(
            event_location_id=event_location.id,
            operating_date=first_day,
        )
        second_operating_day = EventLocationOperatingDay(
            event_location_id=event_location.id,
            operating_date=second_day,
        )
        db.session.add_all([first_operating_day, second_operating_day])
        db.session.flush()

        first_submission = LocationCountSubmission(
            source_location_id=loc.id,
            location_id=loc.id,
            event_location_id=event_location.id,
            event_operating_day_id=first_operating_day.id,
            submission_type=LocationCountSubmission.TYPE_INVENTORY,
            submitted_name="Day One",
            submission_date=first_day,
            status=LocationCountSubmission.STATUS_APPROVED,
            approval_mode=LocationCountSubmission.APPROVAL_MODE_ADD,
        )
        second_submission = LocationCountSubmission(
            source_location_id=loc.id,
            location_id=loc.id,
            event_location_id=event_location.id,
            event_operating_day_id=second_operating_day.id,
            submission_type=LocationCountSubmission.TYPE_INVENTORY,
            submitted_name="Day Two",
            submission_date=second_day,
            status=LocationCountSubmission.STATUS_APPROVED,
            approval_mode=LocationCountSubmission.APPROVAL_MODE_ADD,
        )
        db.session.add_all([first_submission, second_submission])
        db.session.flush()
        db.session.add_all(
            [
                LocationCountSubmissionRow(
                    submission_id=first_submission.id,
                    item_id=item.id,
                    count_value=2,
                    submitted_count_value=2,
                    parse_index=0,
                ),
                LocationCountSubmissionRow(
                    submission_id=second_submission.id,
                    item_id=item.id,
                    count_value=7,
                    submitted_count_value=7,
                    parse_index=0,
                ),
                LocationCountSubmissionRow(
                    submission_id=second_submission.id,
                    item_id=added_item.id,
                    count_value=3,
                    submitted_count_value=3,
                    parse_index=1,
                ),
            ]
        )
        db.session.commit()
        _grant_event_permissions(user)
        event_id = event.id
        location_id = loc.id

    with client:
        login(client, "inventory-approved-display@example.com", "pass")
        first_response = client.get(
            f"/events/{event_id}/count_sheet/{location_id}"
            f"?operating_date={first_day.isoformat()}"
        )
        second_response = client.get(
            f"/events/{event_id}/count_sheet/{location_id}"
            f"?operating_date={second_day.isoformat()}"
        )

    assert first_response.status_code == 200
    assert b"Approved Display Cup" in first_response.data
    assert b"Expected" in first_response.data
    assert b"Approved" in first_response.data
    assert b"Qty Var" in first_response.data
    assert b"Cost Var" in first_response.data
    assert b"2.0000" in first_response.data
    assert b"7.0000" not in first_response.data
    assert b"Approved Added Napkin" not in first_response.data

    assert second_response.status_code == 200
    assert b"Approved Display Cup" in second_response.data
    assert b"7.0000" in second_response.data
    assert b"Approved Added Napkin" in second_response.data
    assert b"3.0000" in second_response.data


def test_inventory_count_sheet_does_not_resurrect_removed_approved_item(client, app):
    count_day = date(2026, 6, 3)
    with app.app_context():
        user = User(
            email="inventory-approved-removed@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Approved Removed Stand")
        item = Item(name="Approved Removed Cup", base_unit="each")
        event = Event(
            name="Approved Removed Inventory",
            start_date=count_day,
            end_date=count_day,
            event_type="inventory",
        )
        db.session.add_all([user, loc, item, event])
        db.session.flush()
        unit = ItemUnit(item_id=item.id, name="each", factor=1)
        location_item = LocationStandItem(
            location_id=loc.id,
            item_id=item.id,
            expected_count=5,
            active=False,
        )
        event_location = EventLocation(event_id=event.id, location_id=loc.id)
        db.session.add_all([unit, location_item, event_location])
        db.session.flush()
        operating_day = EventLocationOperatingDay(
            event_location_id=event_location.id,
            operating_date=count_day,
        )
        db.session.add(operating_day)
        db.session.flush()
        submission = LocationCountSubmission(
            source_location_id=loc.id,
            location_id=loc.id,
            event_location_id=event_location.id,
            event_operating_day_id=operating_day.id,
            submission_type=LocationCountSubmission.TYPE_INVENTORY,
            submitted_name="Approved Counter",
            submission_date=count_day,
            status=LocationCountSubmission.STATUS_APPROVED,
            approval_mode=LocationCountSubmission.APPROVAL_MODE_ADD,
        )
        db.session.add(submission)
        db.session.flush()
        db.session.add(
            LocationCountSubmissionRow(
                submission_id=submission.id,
                item_id=item.id,
                count_value=6,
                submitted_count_value=6,
                expected_count_value=5,
                parse_index=0,
            )
        )
        db.session.commit()
        _grant_event_permissions(user)
        event_id = event.id
        location_id = loc.id

    with client:
        login(client, "inventory-approved-removed@example.com", "pass")
        response = client.get(
            f"/events/{event_id}/count_sheet/{location_id}"
            f"?operating_date={count_day.isoformat()}"
        )

    assert response.status_code == 200
    assert b"Approved Removed Cup" not in response.data


def test_inventory_count_sheet_uses_requested_operating_date(client, app):
    today = date.today()
    count_date = today + timedelta(days=1)
    with app.app_context():
        user = User(
            email="inventory-date@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="Inventory Date Stand")
        item = Item(name="Inventory Date Cup", base_unit="each")
        event = Event(
            name="Monthly Inventory Dates",
            start_date=today,
            end_date=count_date,
            event_type="inventory",
        )
        db.session.add_all([user, loc, item, event])
        db.session.flush()
        unit = ItemUnit(item_id=item.id, name="each", factor=1)
        event_location = EventLocation(event_id=event.id, location_id=loc.id)
        db.session.add_all(
            [
                unit,
                LocationStandItem(
                    location_id=loc.id,
                    item_id=item.id,
                    expected_count=5,
                ),
                event_location,
            ]
        )
        db.session.commit()
        _grant_event_permissions(user)
        event_id = event.id
        location_id = loc.id
        item_id = item.id
        unit_id = unit.id

    with client:
        login(client, "inventory-date@example.com", "pass")
        response = client.post(
            f"/events/{event_id}/count_sheet/{location_id}?operating_date={count_date.isoformat()}",
            data={
                "submitted_name": "Inventory Counter",
                f"inventory_unit_{item_id}_0": str(unit_id),
                f"inventory_qty_{item_id}_0": 4,
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        submission = LocationCountSubmission.query.one()
        assert submission.submission_date == count_date
        assert submission.event_operating_day is not None
        assert submission.event_operating_day.operating_date == count_date


def test_close_event_removes_zero_count_items(client, app):
    with app.app_context():
        user = User(
            email="zero@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="ZeroLoc")
        item = Item(name="ZeroItem", base_unit="each")
        product = Product(name="ZeroProd", price=1.0, cost=1.0)
        db.session.add_all([user, loc, item, product])
        db.session.commit()
        iu = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        pri = ProductRecipeItem(
            product_id=product.id,
            item_id=item.id,
            unit_id=iu.id,
            quantity=1,
            countable=True,
        )
        lsi = LocationStandItem(
            location_id=loc.id, item_id=item.id, expected_count=5
        )
        loc.products.append(product)
        db.session.add_all([iu, pri, lsi])
        db.session.commit()
        _grant_event_permissions(user)
        loc_id = loc.id
        item_id = item.id
        unit_id = iu.id

    with client:
        login(client, "zero@example.com", "pass")
        client.post(
            "/events/create",
            data={
                "name": "ZeroEvent",
                "start_date": "2023-01-01",
                "end_date": "2023-01-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="ZeroEvent").first()
        eid = ev.id

    with client:
        login(client, "zero@example.com", "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        client.post(
            f"/events/{eid}/count_sheet/{loc_id}",
            data={
                "submitted_name": "Inventory Counter",
                f"inventory_unit_{item_id}_0": str(unit_id),
                f"inventory_qty_{item_id}_0": 0,
            },
            follow_redirects=True,
        )
        _approve_inventory_submission(app)
        client.post(f"/events/{eid}/close", follow_redirects=True)

    with app.app_context():
        lsi = LocationStandItem.query.filter_by(
            location_id=loc_id, item_id=item_id
        ).first()
        assert lsi is not None
        assert lsi.expected_count == 0


def test_close_event_removes_unentered_items(client, app):
    with app.app_context():
        user = User(
            email="nocount@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="NoCountLoc")
        item = Item(name="NoCountItem", base_unit="each")
        db.session.add_all([user, loc, item])
        db.session.commit()
        iu = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        lsi = LocationStandItem(
            location_id=loc.id, item_id=item.id, expected_count=5
        )
        db.session.add_all([iu, lsi])
        db.session.commit()
        _grant_event_permissions(user)
        loc_id = loc.id
        item_id = item.id

    with client:
        login(client, "nocount@example.com", "pass")
        client.post(
            "/events/create",
            data={
                "name": "NoCountEvent",
                "start_date": "2023-01-01",
                "end_date": "2023-01-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="NoCountEvent").first()
        eid = ev.id

    with client:
        login(client, "nocount@example.com", "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        # Do not submit a count sheet for this location
        with app.app_context():
            el = EventLocation.query.filter_by(
                event_id=eid, location_id=loc_id
            ).first()
            el.confirmed = True
            db.session.commit()
        client.post(f"/events/{eid}/close", follow_redirects=True)

    with app.app_context():
        lsi = LocationStandItem.query.filter_by(
            location_id=loc_id, item_id=item_id
        ).first()
        assert lsi is not None
        assert lsi.expected_count == 5


def test_close_event_requires_confirmed_locations(client, app):
    with app.app_context():
        user = User(
            email="needsconfirm@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="NeedsConfirm")
        item = Item(name="ConfirmItem", base_unit="each")
        db.session.add_all([user, loc, item])
        db.session.commit()
        _grant_event_permissions(user)
        loc_id = loc.id

    with client:
        login(client, "needsconfirm@example.com", "pass")
        client.post(
            "/events/create",
            data={
                "name": "ConfirmEvent",
                "start_date": "2023-01-01",
                "end_date": "2023-01-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="ConfirmEvent").first()
        eid = ev.id

    with client:
        login(client, "needsconfirm@example.com", "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        response = client.post(f"/events/{eid}/close", follow_redirects=True)
        assert (
            b"All locations must be confirmed before closing the event." in response.data
        )

    with app.app_context():
        ev = db.session.get(Event, eid)
        assert not ev.closed


def test_count_sheet_redirects_to_event_view(client, app):
    with app.app_context():
        user = User(
            email="redir@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="RedirLoc")
        item = Item(name="RedirItem", base_unit="each")
        db.session.add_all([user, loc, item])
        db.session.commit()
        iu = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        lsi = LocationStandItem(
            location_id=loc.id, item_id=item.id, expected_count=5
        )
        db.session.add_all([iu, lsi])
        db.session.commit()
        _grant_event_permissions(user)
        loc_id = loc.id
        item_id = item.id
        unit_id = iu.id

    with client:
        login(client, "redir@example.com", "pass")
        client.post(
            "/events/create",
            data={
                "name": "RedirEvent",
                "start_date": "2023-01-01",
                "end_date": "2023-01-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="RedirEvent").first()
        eid = ev.id

    with client:
        login(client, "redir@example.com", "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        resp = client.post(
            f"/events/{eid}/count_sheet/{loc_id}",
            data={
                "submitted_name": "Inventory Counter",
                f"inventory_unit_{item_id}_0": str(unit_id),
                f"inventory_qty_{item_id}_0": 0,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/events/{eid}")
