from datetime import date, datetime

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    Item,
    Location,
    Product,
    ProductRecipeItem,
    TerminalSale,
)
from app.utils.recipe_history import sync_terminal_sale_recipe_snapshots


def test_terminal_sale_snapshot_preserves_event_sheet_tracking(app):
    with app.app_context():
        location = Location(name="Snapshot Tracking Location")
        event = Event(
            name="Snapshot Tracking Event",
            start_date=date(2026, 8, 15),
            end_date=date(2026, 8, 15),
            event_type="other",
        )
        item = Item(name="Snapshot Tracking Item", base_unit="each", cost=1)
        product = Product(
            name="Snapshot Tracking Product",
            price=5,
            cost=1,
            recipe_yield_quantity=1,
        )
        db.session.add_all([location, event, item, product])
        db.session.flush()
        event_location = EventLocation(event=event, location=location)
        recipe = ProductRecipeItem(
            product=product,
            item=item,
            quantity=2,
            countable=False,
        )
        sheet = EventStandSheetItem(
            event_location=event_location,
            item=item,
        )
        db.session.add_all([event_location, recipe, sheet])
        db.session.flush()
        sale = TerminalSale(
            event_location=event_location,
            product=product,
            quantity=1,
            sold_at=datetime(2026, 8, 15, 12, 0),
        )
        db.session.add(sale)
        db.session.flush()

        sync_terminal_sale_recipe_snapshots(sale, product=product)
        db.session.flush()

        assert sale.recipe_snapshot_captured is True
        assert len(sale.recipe_item_snapshots) == 1
        assert sale.recipe_item_snapshots[0].countable is True
