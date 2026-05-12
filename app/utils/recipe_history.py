from __future__ import annotations

from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    InvoiceProduct,
    InvoiceProductRecipeItemSnapshot,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Menu,
    Product,
    ProductRecipeItem,
    TerminalSale,
    TerminalSaleRecipeItemSnapshot,
)
from app.utils.menu_assignments import get_authoritative_location_products
from app.utils.numeric import coerce_float


def _recipe_snapshot_values(recipe_item: ProductRecipeItem) -> dict[str, object]:
    item = recipe_item.item
    unit = recipe_item.unit
    return {
        "item_id": recipe_item.item_id,
        "unit_id": recipe_item.unit_id,
        "item_name": item.name if item is not None else f"Item #{recipe_item.item_id}",
        "base_unit": item.base_unit if item is not None else None,
        "item_cost": float(getattr(item, "cost", 0.0) or 0.0),
        "unit_name": unit.name if unit is not None else None,
        "unit_factor": float(getattr(unit, "factor", 1.0) or 1.0),
        "quantity": float(recipe_item.quantity or 0.0),
        "countable": bool(recipe_item.countable),
    }


def sync_invoice_product_recipe_snapshots(
    invoice_product: InvoiceProduct, product: Product | None = None
) -> None:
    if invoice_product.is_custom_line or invoice_product.product_id is None:
        return
    product_obj = product or invoice_product.product
    if product_obj is None:
        return

    InvoiceProductRecipeItemSnapshot.query.filter_by(
        invoice_product_id=invoice_product.id
    ).delete()
    for recipe_item in product_obj.recipe_items:
        db.session.add(
            InvoiceProductRecipeItemSnapshot(
                invoice_product_id=invoice_product.id,
                **_recipe_snapshot_values(recipe_item),
            )
        )


def backfill_invoice_product_recipe_snapshots(product: Product) -> None:
    invoice_products = (
        InvoiceProduct.query.options(
            selectinload(InvoiceProduct.recipe_item_snapshots)
        )
        .filter_by(product_id=product.id, is_custom_line=False)
        .all()
    )
    for invoice_product in invoice_products:
        if invoice_product.recipe_item_snapshots:
            continue
        sync_invoice_product_recipe_snapshots(invoice_product, product=product)


def sync_terminal_sale_recipe_snapshots(
    terminal_sale: TerminalSale, product: Product | None = None
) -> None:
    product_obj = product or terminal_sale.product
    if product_obj is None:
        return

    TerminalSaleRecipeItemSnapshot.query.filter_by(
        terminal_sale_id=terminal_sale.id
    ).delete()
    for recipe_item in product_obj.recipe_items:
        db.session.add(
            TerminalSaleRecipeItemSnapshot(
                terminal_sale_id=terminal_sale.id,
                **_recipe_snapshot_values(recipe_item),
            )
        )


def sync_closed_event_sheet_snapshots(event_location: EventLocation, price_lookup: dict[int, float]) -> None:
    for sheet in event_location.stand_sheet_items:
        item = sheet.item
        if item is None:
            continue
        sheet.item_name_snapshot = item.name
        sheet.item_base_unit_snapshot = item.base_unit
        sheet.item_cost_snapshot = float(item.cost or 0.0)
        sheet.price_per_unit_snapshot = price_lookup.get(sheet.item_id)


def calculate_product_recipe_cost(product: Product) -> float:
    batch_cost = 0.0
    for recipe_item in product.recipe_items:
        item = recipe_item.item
        if item is None:
            continue
        quantity = coerce_float(recipe_item.quantity)
        if quantity is None:
            continue
        factor = 1.0
        unit = recipe_item.unit
        if unit is not None:
            factor = coerce_float(unit.factor) or 1.0
        batch_cost += float(item.cost or 0.0) * quantity * factor

    yield_quantity = coerce_float(product.recipe_yield_quantity)
    if yield_quantity is None or yield_quantity <= 0:
        yield_quantity = 1.0
    return batch_cost / float(yield_quantity)


def item_open_event_dependencies(item: Item) -> list[str]:
    dependencies: list[str] = []

    open_sheet_count = (
        db.session.query(EventStandSheetItem.id)
        .join(EventLocation, EventStandSheetItem.event_location_id == EventLocation.id)
        .join(Event, EventLocation.event_id == Event.id)
        .filter(Event.closed.is_(False), EventStandSheetItem.item_id == item.id)
        .count()
    )
    if open_sheet_count:
        dependencies.append(f"{open_sheet_count} open event stand-sheet row(s)")

    open_location_record_count = (
        db.session.query(LocationStandItem.id)
        .join(EventLocation, EventLocation.location_id == LocationStandItem.location_id)
        .join(Event, EventLocation.event_id == Event.id)
        .filter(Event.closed.is_(False), LocationStandItem.item_id == item.id)
        .distinct()
        .count()
    )
    if open_location_record_count:
        dependencies.append(
            f"{open_location_record_count} open event location inventory record(s)"
        )

    open_locations = (
        EventLocation.query.options(
            selectinload(EventLocation.location)
            .selectinload(Location.products)
            .selectinload(Product.recipe_items),
            selectinload(EventLocation.location)
            .selectinload(Location.current_menu)
            .selectinload(Menu.products)
            .selectinload(Product.recipe_items),
        )
        .join(Event, EventLocation.event_id == Event.id)
        .filter(Event.closed.is_(False))
        .all()
    )
    recipe_event_ids: set[int] = set()
    for event_location in open_locations:
        location = event_location.location
        if location is None:
            continue
        for product in get_authoritative_location_products(location):
            if any(recipe_item.item_id == item.id for recipe_item in product.recipe_items):
                recipe_event_ids.add(event_location.event_id)
                break
    if recipe_event_ids:
        dependencies.append(
            f"{len(recipe_event_ids)} open event(s) still sell products using this item"
        )

    return dependencies


def archive_item_for_current_operations(item: Item) -> tuple[list[int], int]:
    affected_products = (
        Product.query.options(
            selectinload(Product.recipe_items).selectinload(ProductRecipeItem.item),
            selectinload(Product.recipe_items).selectinload(ProductRecipeItem.unit),
        )
        .join(ProductRecipeItem, ProductRecipeItem.product_id == Product.id)
        .filter(ProductRecipeItem.item_id == item.id)
        .all()
    )
    affected_product_ids = [product.id for product in affected_products]

    for product in affected_products:
        backfill_invoice_product_recipe_snapshots(product)

    ProductRecipeItem.query.filter_by(item_id=item.id).delete(synchronize_session=False)
    removed_location_count = (
        LocationStandItem.query.filter_by(item_id=item.id).delete(synchronize_session=False)
    )
    item.archived = True

    for product in affected_products:
        db.session.expire(product, ["recipe_items"])
        if product.auto_update_recipe_cost:
            product.cost = calculate_product_recipe_cost(product)

    return affected_product_ids, int(removed_location_count or 0)
