from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, Location, LocationStandItem, User
from tests.utils import login


def test_item_location_report(client, app):
    with app.app_context():
        user = User(
            email="locreport@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        item = Item(name="ReportItem", base_unit="each")
        loc1 = Location(name="Loc1")
        loc2 = Location(name="Loc2")
        db.session.add_all([user, item, loc1, loc2])
        db.session.commit()
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=loc1.id, item_id=item.id, expected_count=5
                ),
                LocationStandItem(
                    location_id=loc2.id, item_id=item.id, expected_count=3
                ),
            ]
        )
        db.session.commit()
        item_id = item.id
    with client:
        login(client, "locreport@example.com", "pass")
        resp = client.get(f"/items/{item_id}/locations")
        assert resp.status_code == 200
        assert b"Loc1" in resp.data
        assert b"Loc2" in resp.data
        assert b"8" in resp.data
