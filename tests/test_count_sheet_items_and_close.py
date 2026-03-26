from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    EventLocation,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    User,
)
from tests.utils import login


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
        loc_id = loc.id
        item_id = item.id

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
                f"recv_{item_id}": 0,
                f"trans_{item_id}": 0,
                f"base_{item_id}": 0,
            },
            follow_redirects=True,
        )
        with app.app_context():
            el = EventLocation.query.filter_by(
                event_id=eid, location_id=loc_id
            ).first()
            el.confirmed = True
            db.session.commit()
        client.get(f"/events/{eid}/close", follow_redirects=True)

    with app.app_context():
        lsi = LocationStandItem.query.filter_by(
            location_id=loc_id, item_id=item_id
        ).first()
        assert lsi is None


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
        client.get(f"/events/{eid}/close", follow_redirects=True)

    with app.app_context():
        lsi = LocationStandItem.query.filter_by(
            location_id=loc_id, item_id=item_id
        ).first()
        assert lsi is None


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
        response = client.get(f"/events/{eid}/close", follow_redirects=True)
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
        loc_id = loc.id
        item_id = item.id

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
                f"recv_{item_id}": 0,
                f"trans_{item_id}": 0,
                f"base_{item_id}": 0,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith(f"/events/{eid}")
