from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Event,
    GLCode,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    User,
)
from tests.utils import login


def test_inventory_report_variance(client, app):
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
        loc_id = loc.id
        item_id = item.id

    with client:
        login(client, "inv@example.com", "pass")
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
        login(client, "inv@example.com", "pass")
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        client.post(
            f"/events/{eid}/count_sheet/{loc_id}",
            data={
                f"recv_{item_id}": 0,
                f"trans_{item_id}": 4,
                f"base_{item_id}": 0,
            },
            follow_redirects=True,
        )
        resp = client.get(f"/events/{eid}/inventory_report")
        assert resp.status_code == 200
        assert b"-1" in resp.data
        assert b"500000" in resp.data


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
        loc_id = loc.id
        item_id = item.id

    with client:
        login(client, "close@example.com", "pass")
        client.post(
            "/events/create",
            data={
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
        client.post(
            f"/events/{eid}/add_location",
            data={"location_id": loc_id},
            follow_redirects=True,
        )
        client.post(
            f"/events/{eid}/count_sheet/{loc_id}",
            data={
                f"recv_{item_id}": 0,
                f"trans_{item_id}": 7,
                f"base_{item_id}": 0,
            },
            follow_redirects=True,
        )
        client.get(f"/events/{eid}/close", follow_redirects=True)

    with app.app_context():
        lsi = LocationStandItem.query.filter_by(
            location_id=loc_id, item_id=item_id
        ).first()
        assert lsi.expected_count == 7
