from datetime import date

from app import db
from app.models import (
    Event,
    EventLocation,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Menu,
    Product,
    ProductRecipeItem,
    TerminalSale,
)
from app.routes.auth_routes import _apply_event_linked_sales_payload
from app.routes.event_routes import _apply_pending_sales, _get_stand_items
from app.routes.location_routes import _protected_location_item_ids


def _create_countable_product(product_name: str, item_name: str) -> tuple[Product, Item]:
    item = Item(name=item_name, base_unit="each")
    unit = ItemUnit(
        item=item,
        name="each",
        factor=1,
        receiving_default=True,
        transfer_default=True,
    )
    product = Product(name=product_name, price=5.0, cost=1.0)
    db.session.add_all([item, unit, product])
    db.session.flush()
    db.session.add(
        ProductRecipeItem(
            product_id=product.id,
            item_id=item.id,
            unit_id=unit.id,
            quantity=1.0,
            countable=True,
        )
    )
    return product, item


def test_menu_managed_locations_hide_drifted_recipe_items_from_stand_sheets(app):
    with app.app_context():
        menu_product, menu_item = _create_countable_product(
            "Menu Cola", "Menu Cola Item"
        )
        drift_product, drift_item = _create_countable_product(
            "Drift Cola", "Drift Cola Item"
        )
        menu = Menu(name="Managed Menu")
        menu.products.append(menu_product)
        location = Location(name="Managed Stand", current_menu=menu)
        location.products.extend([menu_product, drift_product])
        db.session.add_all([menu, location])
        db.session.flush()
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=location.id,
                    item_id=menu_item.id,
                    expected_count=4.0,
                ),
                LocationStandItem(
                    location_id=location.id,
                    item_id=drift_item.id,
                    expected_count=6.0,
                ),
            ]
        )
        db.session.commit()

        protected_item_ids = _protected_location_item_ids(location)
        _, stand_items = _get_stand_items(location.id)

        assert protected_item_ids == {menu_item.id}
        assert [entry["item"].id for entry in stand_items] == [menu_item.id]


def test_get_stand_items_respects_location_countable_override(app):
    with app.app_context():
        product = Product(name="Override Soda", price=5.0, cost=1.0)
        include_item = Item(name="Override Include", base_unit="each")
        include_unit = ItemUnit(
            item=include_item,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        exclude_item = Item(name="Override Exclude", base_unit="each")
        exclude_unit = ItemUnit(
            item=exclude_item,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        location = Location(name="Override Stand")
        location.products.append(product)
        db.session.add_all(
            [product, include_item, include_unit, exclude_item, exclude_unit, location]
        )
        db.session.flush()
        db.session.add_all(
            [
                ProductRecipeItem(
                    product_id=product.id,
                    item_id=include_item.id,
                    unit_id=include_unit.id,
                    quantity=1.0,
                    countable=False,
                ),
                ProductRecipeItem(
                    product_id=product.id,
                    item_id=exclude_item.id,
                    unit_id=exclude_unit.id,
                    quantity=1.0,
                    countable=True,
                ),
                LocationStandItem(
                    location_id=location.id,
                    item_id=include_item.id,
                    countable=True,
                    expected_count=4.0,
                ),
                LocationStandItem(
                    location_id=location.id,
                    item_id=exclude_item.id,
                    countable=False,
                    expected_count=6.0,
                ),
            ]
        )
        db.session.commit()

        _, stand_items = _get_stand_items(location.id)

        assert [entry["item"].id for entry in stand_items] == [include_item.id]


def test_apply_pending_sales_does_not_link_products_to_menu_managed_locations(app):
    with app.app_context():
        allowed_product, _ = _create_countable_product("Allowed Water", "Allowed Case")
        sold_product, sold_item = _create_countable_product(
            "Diet Pepsi", "355ml Diet Pepsi"
        )
        menu = Menu(name="Concourse Menu")
        menu.products.append(allowed_product)
        location = Location(name="Concourse Stand", current_menu=menu)
        location.products.append(allowed_product)
        event = Event(name="Managed Sales Event", start_date=date.today(), end_date=date.today())
        event_location = EventLocation(event=event, location=location)
        db.session.add_all([menu, location, event, event_location])
        db.session.commit()

        _apply_pending_sales(
            [
                {
                    "event_location_id": event_location.id,
                    "product_id": sold_product.id,
                    "product_name": sold_product.name,
                    "quantity": 3.0,
                }
            ],
            [],
            link_products_to_locations=True,
        )
        db.session.flush()

        assert sold_product not in location.products
        assert (
            LocationStandItem.query.filter_by(
                location_id=location.id,
                item_id=sold_item.id,
            ).first()
            is None
        )
        sale = TerminalSale.query.filter_by(
            event_location_id=event_location.id, product_id=sold_product.id
        ).one()
        assert sale.quantity == 3.0


def test_apply_pending_sales_still_links_products_for_standalone_locations(app):
    with app.app_context():
        sold_product, sold_item = _create_countable_product(
            "Standalone Soda", "Standalone Soda Item"
        )
        location = Location(name="Standalone Stand")
        event = Event(name="Standalone Sales Event", start_date=date.today(), end_date=date.today())
        event_location = EventLocation(event=event, location=location)
        db.session.add_all([location, event, event_location])
        db.session.commit()

        _apply_pending_sales(
            [
                {
                    "event_location_id": event_location.id,
                    "product_id": sold_product.id,
                    "product_name": sold_product.name,
                    "quantity": 2.0,
                }
            ],
            [],
            link_products_to_locations=True,
        )
        db.session.flush()

        assert sold_product in location.products
        stand_item = LocationStandItem.query.filter_by(
            location_id=location.id,
            item_id=sold_item.id,
        ).one()
        assert stand_item.expected_count == 0.0


def test_event_linked_sales_import_does_not_link_products_to_menu_managed_locations(app):
    with app.app_context():
        allowed_product, _ = _create_countable_product("Allowed Beer", "Allowed Beer Item")
        sold_product, sold_item = _create_countable_product(
            "Imported Pepsi", "Imported Pepsi Item"
        )
        menu = Menu(name="Imported Menu")
        menu.products.append(allowed_product)
        location = Location(name="Imported Stand", current_menu=menu)
        location.products.append(allowed_product)
        event = Event(name="Imported Event", start_date=date.today(), end_date=date.today())
        event_location = EventLocation(event=event, location=location)
        db.session.add_all([menu, location, event, event_location])
        db.session.commit()

        _apply_event_linked_sales_payload(
            event_location,
            {
                "terminal_sales": [
                    {
                        "product_id": sold_product.id,
                        "quantity": 4.0,
                    }
                ]
            },
        )
        db.session.flush()

        assert sold_product not in location.products
        assert (
            LocationStandItem.query.filter_by(
                location_id=location.id,
                item_id=sold_item.id,
            ).first()
            is None
        )
        sale = TerminalSale.query.filter_by(
            event_location_id=event_location.id, product_id=sold_product.id
        ).one()
        assert sale.quantity == 4.0
