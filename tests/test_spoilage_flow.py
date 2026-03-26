from datetime import datetime, timedelta
import os

from app import db
from app.models import GLCode, Item, Location, Transfer, TransferItem
from tests.utils import login


def test_spoilage_page_filters(client, app):
    with app.app_context():
        # set up locations
        loc = Location(name="Main")
        spoilage_loc = Location(name="Spoilage", is_spoilage=True)
        db.session.add_all([loc, spoilage_loc])
        db.session.commit()

        # item with purchase gl code
        gl = GLCode.query.filter_by(code="5000").first()
        gl_id = gl.id
        item = Item(name="Milk", base_unit="each", purchase_gl_code_id=gl_id)
        db.session.add(item)
        db.session.commit()

        # transfer to spoilage
        transfer = Transfer(
            from_location_id=loc.id,
            to_location_id=spoilage_loc.id,
            user_id=1,
            completed=True,
        )
        db.session.add(transfer)
        db.session.flush()
        ti = TransferItem(
            transfer_id=transfer.id,
            item_id=item.id,
            quantity=2,
            item_name=item.name,
        )
        db.session.add(ti)
        db.session.commit()

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    login(client, admin_email, admin_pass)

    # unfiltered should show item
    resp = client.get("/spoilage")
    assert b"Milk" in resp.data

    # filter by purchase gl code
    resp = client.get(f"/spoilage?purchase_gl_code={gl_id}")
    assert b"Milk" in resp.data

