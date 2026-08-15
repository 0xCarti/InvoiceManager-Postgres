from datetime import date, datetime

from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    Item,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    TerminalSale,
    TerminalSaleRecipeItemSnapshot,
)


PREVIOUS_REVISION = "7b8c9d0e1f2a"


def test_terminal_sale_snapshot_migration_freezes_location_countability(app):
    with app.app_context():
        location = Location(name="Snapshot Migration Location")
        event = Event(
            name="Snapshot Migration Event",
            start_date=date(2026, 8, 15),
            end_date=date(2026, 8, 15),
            event_type="other",
        )
        item = Item(name="Snapshot Migration Item", base_unit="each", cost=1)
        sheet_item = Item(
            name="Snapshot Migration Sheet Item",
            base_unit="each",
            cost=1,
        )
        product = Product(
            name="Snapshot Migration Product",
            price=5,
            cost=1,
            recipe_yield_quantity=2,
        )
        db.session.add_all([location, event, item, sheet_item, product])
        db.session.flush()
        event_location = EventLocation(event=event, location=location)
        recipe = ProductRecipeItem(
            product=product,
            item=item,
            quantity=4,
            countable=True,
        )
        sheet_recipe = ProductRecipeItem(
            product=product,
            item=sheet_item,
            quantity=1,
            countable=False,
        )
        location_item = LocationStandItem(
            location=location,
            item=item,
            active=True,
            countable=False,
        )
        sheet_row = EventStandSheetItem(
            event_location=event_location,
            item=sheet_item,
        )
        db.session.add_all(
            [event_location, recipe, sheet_recipe, location_item, sheet_row]
        )
        db.session.flush()
        sale = TerminalSale(
            event_location=event_location,
            product=product,
            quantity=1,
            sold_at=datetime(2026, 8, 15, 12, 0),
            recipe_snapshot_captured=True,
        )
        db.session.add(sale)
        db.session.flush()
        snapshot = TerminalSaleRecipeItemSnapshot(
            terminal_sale=sale,
            item=item,
            item_name=item.name,
            base_unit=item.base_unit,
            item_cost=1,
            unit_factor=1,
            quantity=4,
            recipe_yield_quantity=2,
            countable=True,
        )
        sheet_snapshot = TerminalSaleRecipeItemSnapshot(
            terminal_sale=sale,
            item=sheet_item,
            item_name=sheet_item.name,
            base_unit=sheet_item.base_unit,
            item_cost=1,
            unit_factor=1,
            quantity=1,
            recipe_yield_quantity=2,
            countable=False,
        )
        db.session.add_all([snapshot, sheet_snapshot])
        db.session.commit()
        sale_id = sale.id
        snapshot_id = snapshot.id
        sheet_snapshot_id = sheet_snapshot.id
        db.session.remove()

        downgrade(revision=PREVIOUS_REVISION)
        upgrade()
        db.session.remove()

        with db.engine.connect() as connection:
            migrated_snapshot = connection.execute(
                text(
                    "SELECT countable, recipe_yield_quantity "
                    "FROM terminal_sale_recipe_item_snapshot WHERE id = :id"
                ),
                {"id": snapshot_id},
            ).one()
            captured = connection.execute(
                text(
                    "SELECT recipe_snapshot_captured "
                    "FROM terminal_sale WHERE id = :id"
                ),
                {"id": sale_id},
            ).scalar_one()
            migrated_sheet_countable = connection.execute(
                text(
                    "SELECT countable FROM terminal_sale_recipe_item_snapshot "
                    "WHERE id = :id"
                ),
                {"id": sheet_snapshot_id},
            ).scalar_one()

    assert migrated_snapshot.countable is False
    assert migrated_snapshot.recipe_yield_quantity == 2
    assert migrated_sheet_countable is True
    assert captured is True
