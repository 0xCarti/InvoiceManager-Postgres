from datetime import date, timedelta

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    Item,
    ItemUnit,
    Location,
    LocationCountSubmission,
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
        old_unit = ItemUnit(item=old_item, name="Case of 12", factor=12)
        old_product = Product(name="Old Coffee", price=2.0, cost=1.0)
        current_item = Item(name="Current Menu Lid", base_unit="each")
        current_unit = ItemUnit(item=current_item, name="Sleeve of 50", factor=50)
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
                    countable=True,
                ),
                ProductRecipeItem(
                    product_id=current_product.id,
                    item_id=current_item.id,
                    unit_id=current_unit.id,
                    quantity=1,
                    countable=True,
                ),
            ]
        )
        set_location_menu(loc, old_menu)
        db.session.flush()
        set_location_menu(loc, current_menu)
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
        assert b"Old Menu Cup" in bulk_count_response.data
        assert b"Current Menu Lid" in bulk_count_response.data
        assert b"Case of 12" in bulk_count_response.data
        assert b"Sleeve of 50" in bulk_count_response.data


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
