"""Helpers for merging purchase orders."""

from __future__ import annotations

import json
from typing import Dict, List, Sequence, Tuple

from flask_login import current_user
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    ActivityLog,
    PurchaseInvoiceDraft,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderItemArchive,
)


class PurchaseMergeError(Exception):
    """Raised when a merge request cannot be completed."""


def merge_purchase_orders(
    target_po_id: int,
    source_po_ids: Sequence[int],
) -> PurchaseOrder:
    """Merge purchase orders into a single target order.

    Args:
        target_po_id: The ID of the purchase order to merge into.
        source_po_ids: A list of purchase order IDs to be merged into the target.

    Returns:
        The updated target :class:`PurchaseOrder` instance.

    Raises:
        PurchaseMergeError: If validation fails or any order is missing.
    """

    if not source_po_ids:
        raise PurchaseMergeError("At least one source purchase order must be provided.")

    if target_po_id in source_po_ids:
        raise PurchaseMergeError("Target purchase order cannot be one of the sources.")

    unique_source_ids = list(dict.fromkeys(source_po_ids))
    all_ids = set(unique_source_ids) | {target_po_id}

    with db.session.begin():
        orders: List[PurchaseOrder] = (
            PurchaseOrder.query.options(selectinload(PurchaseOrder.items))
            .filter(PurchaseOrder.id.in_(all_ids))
            .all()
        )

        if len(orders) != len(all_ids):
            missing = sorted(all_ids - {order.id for order in orders})
            raise PurchaseMergeError(
                f"Purchase order(s) not found: {', '.join(map(str, missing))}"
            )

        order_lookup = {order.id: order for order in orders}
        target_order = order_lookup[target_po_id]
        source_orders = [order_lookup[po_id] for po_id in unique_source_ids]

        _validate_orders(target_order, source_orders)

        position_map, aggregated_items = _aggregate_items(target_order, source_orders)

        target_order.delivery_charge = _combined_delivery(target_order, source_orders)

        for item in list(target_order.items):
            db.session.delete(item)
        target_order.items = aggregated_items

        _merge_invoice_drafts(target_order, source_orders, position_map)
        _archive_source_items(source_orders)

        for source in source_orders:
            db.session.delete(source)

        _record_activity(target_order.id, unique_source_ids)

    return target_order


def _validate_orders(
    target_order: PurchaseOrder, source_orders: Sequence[PurchaseOrder]
) -> None:
    if target_order.received:
        raise PurchaseMergeError("Cannot merge into an order that has already been received.")

    vendor_id = target_order.vendor_id
    expected_date = target_order.expected_date
    for source in source_orders:
        if source.received:
            raise PurchaseMergeError("All source purchase orders must be unreceived.")
        if source.vendor_id != vendor_id:
            raise PurchaseMergeError("All purchase orders must share the same vendor.")
        if source.expected_date != expected_date:
            raise PurchaseMergeError("All purchase orders must share the same expected date.")


def _aggregate_items(
    target_order: PurchaseOrder, source_orders: Sequence[PurchaseOrder]
) -> Tuple[Dict[tuple[int, int], int], List[PurchaseOrderItem]]:
    """Aggregate items from the target and sources by identity.

    Items sharing the same (item_id, unit_id, product_id, unit_cost) tuple are combined
    into a single :class:`PurchaseOrderItem` with summed quantities. Positions are
    reassigned sequentially starting at zero.
    """

    all_orders = [target_order, *source_orders]
    order_priority = {target_order.id: -1}
    order_priority.update({order.id: index for index, order in enumerate(source_orders)})

    grouped: Dict[
        Tuple[int | None, int | None, int | None, float | None],
        Dict[str, object],
    ] = {}

    for order in all_orders:
        for item in order.items:
            key = (item.item_id, item.unit_id, item.product_id, item.unit_cost)
            record = grouped.setdefault(key, {"quantity": 0.0, "items": [], "sample": item})
            record["quantity"] = float(record["quantity"]) + float(item.quantity)
            record["items"].append((order.id, item.position))

    def _sort_key(entry: Tuple[tuple, Dict[str, object]]):
        positions: List[Tuple[int, int]] = entry[1]["items"]  # type: ignore[index]
        anchor_order, anchor_position = min(
            positions,
            key=lambda data: (order_priority.get(data[0], data[0]), data[1]),
        )
        return (order_priority.get(anchor_order, anchor_order), anchor_position)

    position_map: Dict[tuple[int, int], int] = {}
    aggregated_items: List[PurchaseOrderItem] = []

    for new_position, (key, info) in enumerate(sorted(grouped.items(), key=_sort_key)):
        sample_item: PurchaseOrderItem = info["sample"]  # type: ignore[assignment]
        aggregated_items.append(
            PurchaseOrderItem(
                purchase_order_id=target_order.id,
                position=new_position,
                product_id=sample_item.product_id,
                unit_id=sample_item.unit_id,
                item_id=sample_item.item_id,
                quantity=info["quantity"],
                unit_cost=sample_item.unit_cost,
            )
        )

        for order_id, previous_position in info["items"]:  # type: ignore[index]
            position_map[(order_id, previous_position)] = new_position

    return position_map, aggregated_items


def _combined_delivery(
    target_order: PurchaseOrder, source_orders: Sequence[PurchaseOrder]
) -> float:
    return (target_order.delivery_charge or 0.0) + sum(
        source.delivery_charge or 0.0 for source in source_orders
    )


def _archive_source_items(source_orders: Sequence[PurchaseOrder]) -> None:
    archives: List[PurchaseOrderItemArchive] = []
    for source in source_orders:
        for item in source.items:
            archives.append(
                PurchaseOrderItemArchive(
                    purchase_order_id=source.id,
                    position=item.position,
                    item_id=item.item_id,
                    unit_id=item.unit_id,
                    quantity=item.quantity,
                    unit_cost=item.unit_cost,
                )
            )
    if archives:
        db.session.bulk_save_objects(archives)


def _record_activity(target_po_id: int, source_po_ids: Sequence[int]) -> None:
    user_id = _current_user_id()
    merged_ids = ", ".join(map(str, source_po_ids))
    activities = [
        ActivityLog(
            user_id=user_id,
            activity=f"Merged purchase orders {merged_ids} into {target_po_id}",
        ),
        ActivityLog(
            user_id=user_id,
            activity=(
                "Combined delivery charges and archived source purchase orders "
                f"{merged_ids} into {target_po_id}"
            ),
        ),
    ]
    db.session.add_all(activities)


def _current_user_id() -> int | None:
    if current_user and not current_user.is_anonymous:
        return current_user.id
    return None


def _merge_invoice_drafts(
    target_order: PurchaseOrder,
    source_orders: List[PurchaseOrder],
    draft_position_map: Dict[tuple[int, int], int],
) -> None:
    """Merge or migrate invoice drafts for the merged purchase orders."""

    target_draft = PurchaseInvoiceDraft.query.filter_by(
        purchase_order_id=target_order.id
    ).first()
    source_drafts = {
        draft.purchase_order_id: draft
        for draft in PurchaseInvoiceDraft.query.filter(
            PurchaseInvoiceDraft.purchase_order_id.in_(
                [order.id for order in source_orders]
            )
        )
    }

    if not target_draft and not source_drafts:
        return

    base_payload = dict(target_draft.data) if target_draft else {}
    base_items = []

    for item in (target_draft.data or {}).get("items", []) if target_draft else []:
        item_copy = dict(item)
        mapped_position = draft_position_map.get((target_order.id, item.get("position")))
        if mapped_position is not None:
            item_copy["position"] = mapped_position
        base_items.append(item_copy)

    draft_sources = []

    for source in source_orders:
        draft = source_drafts.get(source.id)
        if not draft:
            continue
        draft_sources.append(source.id)
        incoming = draft.data or {}
        updated_items = []
        for item in incoming.get("items", []) or []:
            mapped_position = draft_position_map.get(
                (source.id, item.get("position"))
            )
            item_copy = dict(item)
            if mapped_position is not None:
                item_copy["position"] = mapped_position
            updated_items.append(item_copy)

        for key in [
            "invoice_number",
            "received_date",
            "location_id",
            "department",
            "gst",
            "pst",
            "delivery_charge",
        ]:
            incoming_value = incoming.get(key)
            base_value = base_payload.get(key)
            if base_value in (None, "") and incoming_value not in (None, ""):
                base_payload[key] = incoming_value
            elif (
                base_value not in (None, "")
                and incoming_value not in (None, "")
                and incoming_value != base_value
            ):
                raise PurchaseMergeError(
                    "Purchase invoice drafts contain conflicting values and cannot be merged."
                )

        base_items.extend(updated_items)

    positions_in_payload = {
        item.get("position") for item in base_items if item.get("position") is not None
    }
    for item in sorted(target_order.items, key=lambda itm: itm.position):
        if item.position in positions_in_payload:
            continue
        base_items.append(
            {
                "item_id": item.item_id,
                "unit_id": item.unit_id,
                "quantity": item.quantity,
                "cost": item.unit_cost,
                "position": item.position,
                "gl_code_id": None,
                "location_id": None,
            }
        )

    base_items.sort(key=lambda itm: (itm.get("position") is None, itm.get("position", 0)))
    base_payload["items"] = base_items

    if target_draft:
        target_draft.update_payload(base_payload)
    else:
        target_draft = PurchaseInvoiceDraft(
            purchase_order_id=target_order.id, payload=json.dumps(base_payload)
        )
        db.session.add(target_draft)

    if source_drafts:
        for draft in source_drafts.values():
            db.session.delete(draft)

    if draft_sources:
        merged_ids = ", ".join(map(str, draft_sources))
        db.session.add(
            ActivityLog(
                user_id=_current_user_id(),
                activity=(
                    "Merged purchase invoice drafts from purchase orders "
                    f"{merged_ids} into {target_order.id}"
                ),
            )
        )

