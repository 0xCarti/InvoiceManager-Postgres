from datetime import date

import pytest

from app import db
from app.models import InventoryExpiryLot, Item, Location
from app.services.inventory_expiry import (
    SOURCE_SALES_INVOICE,
    consume_item_lots,
    create_received_lot,
    item_default_expiry_date,
    restore_lot_adjustments,
)


def test_shelf_life_default_expiry_date():
    item = Item(
        name="Milk",
        base_unit="each",
        expiry_tracking_mode=Item.EXPIRY_TRACKING_SHELF_LIFE,
        expiry_shelf_life_days=10,
    )

    assert item_default_expiry_date(item, date(2026, 6, 2)) == date(2026, 6, 12)


def test_expiry_lots_consume_fefo_and_restore(app):
    with app.app_context():
        item = Item(
            name="Expiry Test Item",
            base_unit="each",
            expiry_tracking_mode=Item.EXPIRY_TRACKING_EXACT,
        )
        location = Location(name="Expiry Test Location")
        db.session.add_all([item, location])
        db.session.flush()

        later = create_received_lot(
            item=item,
            location_id=location.id,
            quantity=5,
            received_date=date(2026, 6, 2),
            expiry_date=date(2026, 6, 20),
        )
        earlier = create_received_lot(
            item=item,
            location_id=location.id,
            quantity=5,
            received_date=date(2026, 6, 2),
            expiry_date=date(2026, 6, 10),
        )
        db.session.flush()

        consumed = consume_item_lots(
            item_id=item.id,
            location_id=location.id,
            quantity=6,
            source_type=SOURCE_SALES_INVOICE,
            source_id=123,
            source_line_id=456,
        )
        db.session.flush()

        assert consumed == pytest.approx(6)
        assert earlier.remaining_quantity == pytest.approx(0)
        assert later.remaining_quantity == pytest.approx(4)

        restore_lot_adjustments(
            source_type=SOURCE_SALES_INVOICE,
            source_id=123,
            source_line_id=456,
        )
        db.session.flush()

        assert earlier.remaining_quantity == pytest.approx(5)
        assert later.remaining_quantity == pytest.approx(5)
        assert InventoryExpiryLot.query.count() == 2
