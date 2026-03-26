"""Utility helpers for managing menu assignments to locations."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from app import db
from app.models import Location, LocationStandItem, Menu, MenuAssignment


def _collect_menu_items(menu: Optional[Menu]) -> dict[int, int]:
    """Return a mapping of item id to purchase GL code id for a menu."""

    if menu is None:
        return {}
    return _collect_product_items(menu.products)


def _collect_product_items(products: Iterable["Product"]) -> dict[int, int]:
    """Return a mapping of item id to purchase GL code id for products."""

    items: dict[int, int] = {}
    for product in products:
        for recipe_item in product.recipe_items:
            if not recipe_item.countable:
                continue
            items[recipe_item.item_id] = recipe_item.item.purchase_gl_code_id
    return items


def apply_menu_products(
    location: Location,
    menu: Optional[Menu],
    *,
    products: Optional[Iterable["Product"]] = None,
) -> None:
    """Synchronise a location's products and stand sheet with the given menu or products."""

    if menu is not None:
        desired_products = list(menu.products)
        desired_items = _collect_menu_items(menu)
    elif products is not None:
        desired_products = list(products)
        desired_items = _collect_product_items(desired_products)
    else:
        desired_products = []
        desired_items = {}
    # Debug logging removed; ensure desired products/items are applied consistently.
    location.products = desired_products

    existing_records: dict[int, LocationStandItem] = {}
    for record in list(location.stand_items):
        if record in db.session.deleted:
            continue
        existing_records[record.item_id] = record

    # Remove stand sheet items that are no longer required
    for record in list(location.stand_items):
        if record.item_id not in desired_items:
            db.session.delete(record)
            existing_records.pop(record.item_id, None)

    # Ensure all required items exist
    for item_id, purchase_gl_code_id in desired_items.items():
        record = existing_records.get(item_id)
        if record in db.session.deleted:
            record = None
        if record is not None:
            if record.purchase_gl_code_id != purchase_gl_code_id:
                record.purchase_gl_code_id = purchase_gl_code_id
            continue
        db.session.add(
            LocationStandItem(
                location=location,
                item_id=item_id,
                expected_count=0,
                purchase_gl_code_id=purchase_gl_code_id,
            )
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
