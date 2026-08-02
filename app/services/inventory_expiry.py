from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import nulls_last

from app import db
from app.models import (
    InventoryExpiryLot,
    InventoryExpiryLotAdjustment,
    Item,
)


SOURCE_PURCHASE_INVOICE = "purchase_invoice"
SOURCE_TRANSFER = "transfer"
SOURCE_SALES_INVOICE = "sales_invoice"
SOURCE_POS_IMPORT = "pos_import"


def _normalize_source_id(source_id: int | str | None) -> str | None:
    if source_id is None:
        return None
    return str(source_id)


def item_default_expiry_date(item: Item, received_date: date) -> date | None:
    if item.expiry_tracking_mode != Item.EXPIRY_TRACKING_SHELF_LIFE:
        return None
    days = int(item.expiry_shelf_life_days or 0)
    if days <= 0:
        return None
    return received_date + timedelta(days=days)


def resolve_received_expiry_date(
    item: Item, received_date: date, submitted_expiry_date: date | None
) -> date | None:
    if item.expiry_tracking_mode == Item.EXPIRY_TRACKING_SHELF_LIFE:
        return submitted_expiry_date or item_default_expiry_date(item, received_date)
    if item.expiry_tracking_mode == Item.EXPIRY_TRACKING_EXACT:
        return submitted_expiry_date
    return None


def create_received_lot(
    *,
    item: Item,
    location_id: int | None,
    quantity: float,
    received_date: date,
    expiry_date: date | None,
    purchase_invoice_id: int | None = None,
    purchase_invoice_item_id: int | None = None,
) -> InventoryExpiryLot | None:
    quantity = float(quantity or 0.0)
    if quantity <= 0 or item.expiry_tracking_mode == Item.EXPIRY_TRACKING_NONE:
        return None

    lot = InventoryExpiryLot(
        item_id=item.id,
        location_id=location_id,
        purchase_invoice_id=purchase_invoice_id,
        purchase_invoice_item_id=purchase_invoice_item_id,
        received_date=received_date,
        expiry_date=expiry_date,
        original_quantity=quantity,
        remaining_quantity=quantity,
        source_type=SOURCE_PURCHASE_INVOICE,
        source_id=_normalize_source_id(purchase_invoice_id),
        source_line_id=purchase_invoice_item_id,
    )
    db.session.add(lot)
    return lot


def _lot_query(item_id: int, location_id: int | None = None):
    query = InventoryExpiryLot.query.filter(
        InventoryExpiryLot.item_id == item_id,
        InventoryExpiryLot.remaining_quantity > 0,
    )
    if location_id is not None:
        query = query.filter(InventoryExpiryLot.location_id == location_id)
    return query.order_by(
        nulls_last(InventoryExpiryLot.expiry_date.asc()),
        InventoryExpiryLot.received_date.asc(),
        InventoryExpiryLot.id.asc(),
    )


def consume_item_lots(
    *,
    item_id: int,
    quantity: float,
    location_id: int | None = None,
    source_type: str,
    source_id: int | str | None = None,
    source_line_id: int | None = None,
) -> float:
    remaining_to_consume = float(quantity or 0.0)
    if remaining_to_consume <= 0:
        return 0.0

    consumed = 0.0
    for lot in _lot_query(item_id, location_id).all():
        if remaining_to_consume <= 0:
            break
        take = min(float(lot.remaining_quantity or 0.0), remaining_to_consume)
        if take <= 0:
            continue
        lot.remaining_quantity = float(lot.remaining_quantity or 0.0) - take
        consumed += take
        remaining_to_consume -= take
        db.session.add(
            InventoryExpiryLotAdjustment(
                lot=lot,
                quantity_delta=-take,
                source_type=source_type,
                source_id=_normalize_source_id(source_id),
                source_line_id=source_line_id,
            )
        )

    return consumed


def restore_lot_adjustments(
    *,
    source_type: str,
    source_id: int | str | None = None,
    source_line_id: int | None = None,
) -> None:
    query = InventoryExpiryLotAdjustment.query.filter_by(source_type=source_type)
    if source_id is not None:
        query = query.filter_by(source_id=_normalize_source_id(source_id))
    if source_line_id is not None:
        query = query.filter_by(source_line_id=source_line_id)

    for adjustment in query.all():
        lot = adjustment.lot
        if lot is not None:
            lot.remaining_quantity = float(lot.remaining_quantity or 0.0) - float(
                adjustment.quantity_delta or 0.0
            )
        db.session.delete(adjustment)


def move_item_lots(
    *,
    item_id: int,
    from_location_id: int,
    to_location_id: int,
    quantity: float,
    source_type: str,
    source_id: int | str | None,
    source_line_id: int | None,
) -> float:
    quantity = float(quantity or 0.0)
    if quantity <= 0:
        return 0.0

    consumed_rows: list[tuple[InventoryExpiryLot, float]] = []
    remaining_to_move = quantity
    for lot in _lot_query(item_id, from_location_id).all():
        if remaining_to_move <= 0:
            break
        take = min(float(lot.remaining_quantity or 0.0), remaining_to_move)
        if take <= 0:
            continue
        lot.remaining_quantity = float(lot.remaining_quantity or 0.0) - take
        consumed_rows.append((lot, take))
        remaining_to_move -= take
        db.session.add(
            InventoryExpiryLotAdjustment(
                lot=lot,
                quantity_delta=-take,
                source_type=source_type,
                source_id=_normalize_source_id(source_id),
                source_line_id=source_line_id,
            )
        )

    for source_lot, moved_quantity in consumed_rows:
        db.session.add(
            InventoryExpiryLot(
                item_id=item_id,
                location_id=to_location_id,
                received_date=source_lot.received_date,
                expiry_date=source_lot.expiry_date,
                original_quantity=moved_quantity,
                remaining_quantity=moved_quantity,
                source_type=source_type,
                source_id=_normalize_source_id(source_id),
                source_line_id=source_line_id,
            )
        )

    return quantity - remaining_to_move


def reverse_moved_item_lots(
    *,
    source_type: str,
    source_id: int | str | None,
    source_line_id: int | None,
) -> None:
    query = InventoryExpiryLot.query.filter_by(source_type=source_type)
    if source_id is not None:
        query = query.filter_by(source_id=_normalize_source_id(source_id))
    if source_line_id is not None:
        query = query.filter_by(source_line_id=source_line_id)
    for lot in query.all():
        db.session.delete(lot)

    restore_lot_adjustments(
        source_type=source_type,
        source_id=source_id,
        source_line_id=source_line_id,
    )


def expiry_summary(today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    lots = (
        InventoryExpiryLot.query.join(Item)
        .filter(InventoryExpiryLot.remaining_quantity > 0)
        .filter(Item.expiry_tracking_mode != Item.EXPIRY_TRACKING_NONE)
        .all()
    )
    expired = 0
    expiring_soon = 0
    unknown = 0
    for lot in lots:
        if lot.expiry_date is None:
            unknown += 1
            continue
        if lot.expiry_date < today:
            expired += 1
            continue
        warning_days = int(lot.item.expiry_warning_days or 14) if lot.item else 14
        if lot.expiry_date <= today + timedelta(days=warning_days):
            expiring_soon += 1
    return {
        "expired_count": expired,
        "expiring_soon_count": expiring_soon,
        "unknown_count": unknown,
        "tracked_lot_count": len(lots),
    }
