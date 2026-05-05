"""Utility helpers for managing menu assignments to locations."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from app import db
from app.models import Location, LocationStandItem, Menu, MenuAssignment


def _collect_menu_items(menu: Optional[Menu]) -> dict[int, dict[str, object]]:
    """Return authoritative recipe item metadata for a menu."""

    if menu is None:
        return {}
    return _collect_product_items(menu.products)


def _collect_product_items(
    products: Iterable["Product"],
) -> dict[int, dict[str, object]]:
    """Return authoritative recipe item metadata for products."""

    items: dict[int, dict[str, object]] = {}
    for product in products:
        for recipe_item in product.recipe_items:
            item_id = recipe_item.item_id
            item_obj = recipe_item.item
            if item_id is None or item_obj is None:
                continue
            entry = items.setdefault(
                item_id,
                {
                    "purchase_gl_code_id": item_obj.purchase_gl_code_id,
                    "countable": False,
                },
            )
            if (
                entry["purchase_gl_code_id"] is None
                and item_obj.purchase_gl_code_id is not None
            ):
                entry["purchase_gl_code_id"] = item_obj.purchase_gl_code_id
            entry["countable"] = bool(entry["countable"] or recipe_item.countable)
    return items


def get_authoritative_location_products(location: Location | None) -> list["Product"]:
    """Return the product set that should drive a location's stand sheets."""

    if location is None:
        return []
    if location.current_menu is not None:
        return list(location.current_menu.products)
    return list(location.products)


def get_countable_recipe_item_ids(products: Iterable["Product"]) -> set[int]:
    """Return the countable recipe item ids referenced by the products."""

    return {
        item_id
        for item_id, metadata in _collect_product_items(products).items()
        if metadata.get("countable")
    }


def get_recipe_item_ids(products: Iterable["Product"]) -> set[int]:
    """Return every recipe-backed item id referenced by the products."""

    return set(_collect_product_items(products).keys())


def get_location_drift_recipe_item_ids(location: Location | None) -> set[int]:
    """Return recipe-backed item ids added outside the location's current menu."""

    if location is None or location.current_menu is None:
        return set()

    menu_product_ids = {product.id for product in location.current_menu.products}
    drift_products = [
        product for product in location.products if product.id not in menu_product_ids
    ]
    return get_recipe_item_ids(drift_products)


def sync_location_stand_items(
    location: Location | None,
    *,
    products: Optional[Iterable["Product"]] = None,
    remove_missing: bool = False,
) -> dict[int, LocationStandItem]:
    """Ensure stand-item rows exist for the given location products.

    Existing per-location overrides are preserved. New rows inherit the recipe
    default countable flag and the item's purchase GL code.
    """

    if location is None:
        return {}

    desired_products = (
        list(products)
        if products is not None
        else get_authoritative_location_products(location)
    )
    desired_items = _collect_product_items(desired_products)

    existing_records: dict[int, LocationStandItem] = {}
    for record in list(location.stand_items):
        if record in db.session.deleted:
            continue
        existing_records[record.item_id] = record

    if remove_missing:
        for record in list(location.stand_items):
            if record.item_id in desired_items:
                continue
            db.session.delete(record)
            existing_records.pop(record.item_id, None)

    for item_id, metadata in desired_items.items():
        record = existing_records.get(item_id)
        if record in db.session.deleted:
            record = None
        if record is None:
            record = LocationStandItem(
                location=location,
                item_id=item_id,
                countable=bool(metadata.get("countable")),
                expected_count=0,
                purchase_gl_code_id=metadata.get("purchase_gl_code_id"),
            )
            db.session.add(record)
            existing_records[item_id] = record
            continue
        if (
            record.purchase_gl_code_id is None
            and metadata.get("purchase_gl_code_id") is not None
        ):
            record.purchase_gl_code_id = metadata.get("purchase_gl_code_id")

    return existing_records


def apply_menu_products(
    location: Location,
    menu: Optional[Menu],
    *,
    products: Optional[Iterable["Product"]] = None,
) -> None:
    """Synchronise a location's products and stand sheet with the given menu or products."""

    if menu is not None:
        desired_products = list(menu.products)
    elif products is not None:
        desired_products = list(products)
    else:
        desired_products = []

    location.products = desired_products
    sync_location_stand_items(
        location,
        products=desired_products,
        remove_missing=True,
    )

    location.current_menu = menu


def set_location_menu(location: Location, menu: Optional[Menu]) -> None:
    """Assign a menu to a location, recording history and syncing products."""

    db.session.flush([location])
    new_menu_id = menu.id if menu is not None else None
    current_menu_id = location.current_menu_id

    if current_menu_id != new_menu_id:
        now = datetime.utcnow()
        active_assignment = (
            MenuAssignment.query.filter_by(
                location_id=location.id, unassigned_at=None
            )
            .order_by(MenuAssignment.assigned_at.desc())
            .first()
        )
        if active_assignment is not None:
            if active_assignment.menu_id != new_menu_id:
                active_assignment.unassigned_at = now
        if menu is not None:
            db.session.add(
                MenuAssignment(
                    location_id=location.id,
                    menu_id=menu.id,
                    assigned_at=now,
                )
            )
            menu.last_used_at = now
    apply_menu_products(location, menu)


def sync_menu_locations(menu: Menu) -> None:
    """Update all active locations for a menu after the menu changes."""

    for assignment in menu.assignments:
        if assignment.unassigned_at is not None or assignment.location is None:
            continue
        apply_menu_products(assignment.location, menu)
