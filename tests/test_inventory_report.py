from datetime import date

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    GLCode,
    Item,
    ItemUnit,
    Location,
    LocationCountSubmission,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    User,
)
from app.services.location_count_submissions import (
    sync_event_location_counts_from_approved_submissions,
)
from tests.permission_helpers import grant_event_permissions
from tests.utils import extract_csrf_token
from tests.utils import login


def test_inventory_report_summarizes_by_gl_code_only(client, app):
    with app.app_context():
        user = User(
            email="inv@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="InvLoc")
        gl = GLCode(code="500000", description="Beverage")
        db.session.add_all([user, loc, gl])
        db.session.commit()
        item = Item(
            name="Pepsi",
            base_unit="each",
            cost=1.0,
            purchase_gl_code_id=gl.id,
        )
        product = Product(name="Pepsi Product", price=1.0, cost=1.0)
        db.session.add_all([item, product])
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
        grant_event_permissions(user)
        loc_id = loc.id
        item_id = item.id
        unit_id = iu.id

    with client:
        login(client, "inv@example.com", "pass")
        create_page = client.get("/events/create")
        create_token = extract_csrf_token(create_page)
        client.post(
            "/events/create",
            data={
                "csrf_token": create_token,
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
        login(client, "inv@example.com", "pass")
        add_location_page = client.get(f"/events/{eid}")
        add_location_token = extract_csrf_token(add_location_page)
        client.post(
            f"/events/{eid}/add_location",
            data={
                "csrf_token": add_location_token,
                "location_id": loc_id,
            },
            follow_redirects=True,
        )
        count_sheet_page = client.get(
            f"/events/{eid}/count_sheet/{loc_id}"
        )
        count_sheet_token = extract_csrf_token(count_sheet_page)
        client.post(
            f"/events/{eid}/count_sheet/{loc_id}",
            data={
                "csrf_token": count_sheet_token,
                "submitted_name": "Inventory Counter",
                f"inventory_unit_{item_id}_0": str(unit_id),
                f"inventory_qty_{item_id}_0": 4,
            },
            follow_redirects=True,
        )
        with app.app_context():
            submission = LocationCountSubmission.query.one()
            submission.status = LocationCountSubmission.STATUS_APPROVED
            submission.approval_mode = LocationCountSubmission.APPROVAL_MODE_ADD
            sync_event_location_counts_from_approved_submissions(
                submission.event_location_id
            )
            db.session.commit()
        resp = client.get(f"/events/{eid}/inventory_report")
        assert resp.status_code == 200
        assert b"Summary Source 18 - InvEvent" in resp.data
        assert b"Export CSV" in resp.data
        assert b"500000" in resp.data
        assert b"Beverage" in resp.data
        assert b"4.0000" in resp.data
        assert b"4.00" in resp.data
        assert b"Pepsi" not in resp.data
        assert b"InvLoc" not in resp.data
        assert b"Expected" not in resp.data
        assert b"Variance" not in resp.data

        csv_resp = client.get(f"/events/{eid}/inventory_report.csv")
        assert csv_resp.status_code == 200
        assert csv_resp.headers["Content-Type"].startswith("text/csv")
        csv_body = csv_resp.data.decode()
        assert "GL Code,GL Code Description,Total Quantity,Total Cost" in csv_body
        assert "500000,Beverage,4.0000,4.00" in csv_body
        assert "Grand Total,,4.0000,4.00" in csv_body
        assert "Pepsi" not in csv_body


def test_inventory_report_sorts_and_totals_gl_codes(client, app):
    with app.app_context():
        user = User(
            email="inventory-summary-sort@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Hidden Summary Location")
        gl_100 = GLCode(code="100000", description="Food")
        gl_500 = GLCode(code="500000", description="Beverage")
        item_a = Item(
            name="Hidden GL Item A",
            base_unit="each",
            cost=1.5,
            purchase_gl_code=gl_100,
        )
        item_b = Item(
            name="Hidden GL Item B",
            base_unit="each",
            cost=3.0,
            purchase_gl_code=gl_500,
        )
        item_c = Item(
            name="Hidden GL Item C",
            base_unit="each",
            cost=2.0,
            purchase_gl_code=gl_100,
        )
        item_unassigned = Item(
            name="Hidden Unassigned Item",
            base_unit="each",
            cost=1.0,
        )
        event = Event(
            name="Sorted Summary Inventory",
            start_date=date(2026, 3, 31),
            end_date=date(2026, 3, 31),
            event_type="inventory",
        )
        db.session.add_all(
            [
                user,
                location,
                gl_100,
                gl_500,
                item_a,
                item_b,
                item_c,
                item_unassigned,
                event,
            ]
        )
        db.session.flush()
        event_location = EventLocation(event=event, location=location)
        db.session.add(event_location)
        db.session.flush()
        db.session.add_all(
            [
                EventStandSheetItem(
                    event_location_id=event_location.id,
                    item_id=item_b.id,
                    closing_count=2,
                    item_cost_snapshot=3.0,
                ),
                EventStandSheetItem(
                    event_location_id=event_location.id,
                    item_id=item_a.id,
                    closing_count=4,
                    item_cost_snapshot=1.5,
                ),
                EventStandSheetItem(
                    event_location_id=event_location.id,
                    item_id=item_unassigned.id,
                    closing_count=7,
                    item_cost_snapshot=1.0,
                ),
                EventStandSheetItem(
                    event_location_id=event_location.id,
                    item_id=item_c.id,
                    closing_count=1,
                    item_cost_snapshot=2.0,
                ),
            ]
        )
        db.session.commit()
        grant_event_permissions(user)
        event_id = event.id

    with client:
        login(client, "inventory-summary-sort@example.com", "pass")
        resp = client.get(f"/events/{event_id}/inventory_report")

    assert resp.status_code == 200
    body = resp.data.decode()
    assert body.index("100000") < body.index("500000") < body.index("Unassigned")
    assert body.count("100000") == 1
    assert "Food" in body
    assert "Beverage" in body
    assert "5.0000" in body
    assert "8.00" in body
    assert "2.0000" in body
    assert "6.00" in body
    assert "7.0000" in body
    assert "7.00" in body
    assert "14.0000" in body
    assert "21.00" in body
    assert "Hidden Summary Location" not in body
    assert "Hidden GL Item A" not in body
    assert "Hidden GL Item B" not in body
    assert "Hidden GL Item C" not in body
    assert "Hidden Unassigned Item" not in body


def test_inventory_comparison_report_compares_previous_inventory_event(client, app):
    with app.app_context():
        user = User(
            email="inventory-comparison@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Inventory Comparison Location")
        gl = GLCode(code="501800", description="Inventory Food")
        item = Item(
            name="Comparison Pretzel",
            base_unit="each",
            cost=2.0,
            purchase_gl_code=gl,
        )
        current_only = Item(
            name="Comparison Current Only",
            base_unit="each",
            cost=3.0,
            purchase_gl_code=gl,
        )
        previous_event = Event(
            name="Previous Inventory",
            start_date=date(2026, 1, 31),
            end_date=date(2026, 1, 31),
            event_type="inventory",
        )
        current_event = Event(
            name="Current Inventory",
            start_date=date(2026, 2, 28),
            end_date=date(2026, 2, 28),
            event_type="inventory",
        )
        db.session.add_all(
            [user, location, gl, item, current_only, previous_event, current_event]
        )
        db.session.flush()
        previous_location = EventLocation(
            event_id=previous_event.id,
            location_id=location.id,
        )
        current_location = EventLocation(
            event_id=current_event.id,
            location_id=location.id,
        )
        db.session.add_all([previous_location, current_location])
        db.session.flush()
        db.session.add_all(
            [
                EventStandSheetItem(
                    event_location_id=previous_location.id,
                    item_id=item.id,
                    closing_count=5,
                    item_name_snapshot="Comparison Pretzel",
                    item_base_unit_snapshot="each",
                    item_cost_snapshot=2.0,
                ),
                EventStandSheetItem(
                    event_location_id=current_location.id,
                    item_id=item.id,
                    closing_count=8,
                    item_name_snapshot="Comparison Pretzel",
                    item_base_unit_snapshot="each",
                    item_cost_snapshot=2.0,
                ),
                EventStandSheetItem(
                    event_location_id=current_location.id,
                    item_id=current_only.id,
                    closing_count=4,
                    item_name_snapshot="Comparison Current Only",
                    item_base_unit_snapshot="each",
                    item_cost_snapshot=3.0,
                ),
            ]
        )
        db.session.commit()
        grant_event_permissions(user)
        event_id = current_event.id

    with client:
        login(client, "inventory-comparison@example.com", "pass")
        resp = client.get(f"/events/{event_id}/inventory_comparison_report")

    assert resp.status_code == 200
    assert b"Inventory Comparison - Current Inventory" in resp.data
    assert b"Export CSV" in resp.data
    assert b"Compared with Previous Inventory" in resp.data
    assert b"Comparison Pretzel" in resp.data
    assert b"5.0000" in resp.data
    assert b"8.0000" in resp.data
    assert b"+3.0000" in resp.data
    assert b"$10.00" in resp.data
    assert b"$16.00" in resp.data
    assert b"+6.00" in resp.data
    assert b"Comparison Current Only" in resp.data
    assert b"$12.00" in resp.data

    with client:
        login(client, "inventory-comparison@example.com", "pass")
        csv_resp = client.get(
            f"/events/{event_id}/inventory_comparison_report.csv"
        )
    assert csv_resp.status_code == 200
    assert csv_resp.headers["Content-Type"].startswith("text/csv")
    csv_body = csv_resp.data.decode()
    assert (
        "Item,Base Unit,GL Code,Previous Quantity,Current Quantity,"
        "Quantity Change,Previous Cost,Current Cost,Cost Change"
    ) in csv_body
    assert "Comparison Pretzel,each,501800,5.0000,8.0000,+3.0000,10.00,16.00,+6.00" in csv_body
    assert "Totals,,,5.0000,12.0000,+7.0000,10.00,28.00,+18.00" in csv_body


def test_inventory_close_updates_counts(client, app):
    with app.app_context():
        user = User(
            email="close@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        loc = Location(name="CloseLoc")
        item = Item(name="Coffee", base_unit="each", cost=1.0)
        product = Product(name="Coffee Product", price=1.0, cost=1.0)
        db.session.add_all([user, loc, item, product])
        db.session.commit()
        recv_unit = ItemUnit(
            item_id=item.id,
            name="case",
            factor=24,
            receiving_default=True,
        )
        trans_unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            transfer_default=True,
        )
        pri = ProductRecipeItem(
            product_id=product.id,
            item_id=item.id,
            unit_id=trans_unit.id,
            quantity=1,
            countable=True,
        )
        lsi = LocationStandItem(
            location_id=loc.id, item_id=item.id, expected_count=5
        )
        loc.products.append(product)
        db.session.add_all([recv_unit, trans_unit, pri, lsi])
        db.session.commit()
        grant_event_permissions(user)
        loc_id = loc.id
        item_id = item.id
        trans_unit_id = trans_unit.id

    with client:
        login(client, "close@example.com", "pass")
        create_page = client.get("/events/create")
        create_token = extract_csrf_token(create_page)
        client.post(
            "/events/create",
            data={
                "csrf_token": create_token,
                "name": "CloseEvent",
                "start_date": "2023-02-01",
                "end_date": "2023-02-02",
                "event_type": "inventory",
            },
            follow_redirects=True,
        )

    with app.app_context():
        ev = Event.query.filter_by(name="CloseEvent").first()
        eid = ev.id

    with client:
        login(client, "close@example.com", "pass")
        add_location_page = client.get(f"/events/{eid}")
        add_location_token = extract_csrf_token(add_location_page)
        client.post(
            f"/events/{eid}/add_location",
            data={
                "csrf_token": add_location_token,
                "location_id": loc_id,
            },
            follow_redirects=True,
        )
        with app.app_context():
            event_location_id = db.session.get(Event, eid).locations[0].id
        count_sheet_page = client.get(
            f"/events/{eid}/count_sheet/{loc_id}"
        )
        count_sheet_token = extract_csrf_token(count_sheet_page)
        client.post(
            f"/events/{eid}/count_sheet/{loc_id}",
            data={
                "csrf_token": count_sheet_token,
                "submitted_name": "Inventory Counter",
                f"inventory_unit_{item_id}_0": str(trans_unit_id),
                f"inventory_qty_{item_id}_0": 7,
            },
            follow_redirects=True,
        )
        with app.app_context():
            submission = LocationCountSubmission.query.one()
            submission.status = LocationCountSubmission.STATUS_APPROVED
            submission.approval_mode = LocationCountSubmission.APPROVAL_MODE_ADD
            sync_event_location_counts_from_approved_submissions(
                submission.event_location_id
            )
            event_location = db.session.get(EventLocation, event_location_id)
            event_location.confirmed = True
            db.session.commit()

        close_page = client.get(f"/events/{eid}")
        close_token = extract_csrf_token(close_page)
        client.post(
            f"/events/{eid}/close",
            data={"csrf_token": close_token},
            follow_redirects=True,
        )

    with app.app_context():
        lsi = LocationStandItem.query.filter_by(
            location_id=loc_id, item_id=item_id
        ).first()
        assert lsi.expected_count == 7
