"""Utilities for generating purchase order demand forecasts."""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app import db
from app.models import (
    EventLocation,
    Item,
    ItemUnit,
    Location,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    TerminalSale,
    Transfer,
    TransferItem,
)


@dataclass(frozen=True)
class ForecastRecommendation:
    """A single forecast recommendation entry."""

    item: Item
    location: Location
    history: Mapping[str, float]
    base_consumption: float
    adjusted_demand: float
    recommended_quantity: float
    suggested_delivery_date: _dt.date
    default_unit_id: Optional[int]


def _coalesce_factor(column):
    """Return a SQL expression that coalesces unit factors to 1."""

    return func.coalesce(column, 1.0)


class DemandForecastingHelper:
    """Aggregate recent activity to support demand forecasting.

    The helper consolidates quantities by item/location across several data
    sources and applies simple multiplicative "what-if" adjustments for
    attendance, weather, and promotional impacts.
    """

    def __init__(
        self,
        session: Optional[Session] = None,
        *,
        lookback_days: int = 30,
        lead_time_days: int = 3,
    ) -> None:
        self.session = session or db.session
        self.lookback_days = lookback_days
        self.lead_time_days = lead_time_days
        self._since = _dt.datetime.utcnow() - _dt.timedelta(days=lookback_days)

    # ------------------------------------------------------------------
    # Data extractors
    # ------------------------------------------------------------------
    def _terminal_sales_totals(
        self, location_ids: Optional[Sequence[int]], item_ids: Optional[Sequence[int]]
    ) -> Iterable[Tuple[int, int, float, _dt.datetime]]:
        factor = _coalesce_factor(ItemUnit.factor)
        query = (
            self.session.query(
                ProductRecipeItem.item_id.label("item_id"),
                EventLocation.location_id.label("location_id"),
                func.sum(
                    TerminalSale.quantity
                    * ProductRecipeItem.quantity
                    * factor
                ).label("quantity"),
                func.max(TerminalSale.sold_at).label("last_activity"),
            )
            .join(EventLocation, TerminalSale.event_location_id == EventLocation.id)
            .join(Product, TerminalSale.product_id == Product.id)
            .join(ProductRecipeItem, ProductRecipeItem.product_id == Product.id)
            .outerjoin(ItemUnit, ProductRecipeItem.unit_id == ItemUnit.id)
            .filter(TerminalSale.sold_at >= self._since)
            .group_by(ProductRecipeItem.item_id, EventLocation.location_id)
        )

        if location_ids:
            query = query.filter(EventLocation.location_id.in_(location_ids))
        if item_ids:
            query = query.filter(ProductRecipeItem.item_id.in_(item_ids))

        return query

    def _transfer_totals(
        self, location_ids: Optional[Sequence[int]], item_ids: Optional[Sequence[int]]
    ) -> Tuple[Iterable[Tuple[int, int, float]], Iterable[Tuple[int, int, float]]]:
        base_query = (
            self.session.query(
                TransferItem.item_id,
                Transfer.from_location_id,
                Transfer.to_location_id,
                TransferItem.quantity,
            )
            .join(Transfer, TransferItem.transfer_id == Transfer.id)
            .filter(Transfer.date_created >= self._since)
        )

        if location_ids:
            base_query = base_query.filter(
                Transfer.from_location_id.in_(location_ids)
                | Transfer.to_location_id.in_(location_ids)
            )
        if item_ids:
            base_query = base_query.filter(TransferItem.item_id.in_(item_ids))

        incoming = {}
        outgoing = {}
        for item_id, from_loc, to_loc, quantity in base_query:
            if to_loc is not None:
                if not location_ids or to_loc in location_ids:
                    incoming.setdefault((item_id, to_loc), 0.0)
                    incoming[(item_id, to_loc)] += float(quantity)
            if from_loc is not None:
                if not location_ids or from_loc in location_ids:
                    outgoing.setdefault((item_id, from_loc), 0.0)
                    outgoing[(item_id, from_loc)] += float(quantity)

        return incoming.items(), outgoing.items()

    def _invoice_totals(
        self, location_ids: Optional[Sequence[int]], item_ids: Optional[Sequence[int]]
    ) -> Iterable[Tuple[int, int, float]]:
        factor = _coalesce_factor(ItemUnit.factor)
        effective_location = func.coalesce(
            PurchaseInvoiceItem.location_id, PurchaseInvoice.location_id
        )

        query = (
            self.session.query(
                PurchaseInvoiceItem.item_id,
                effective_location.label("location_id"),
                func.sum(PurchaseInvoiceItem.quantity * factor).label("quantity"),
            )
            .join(PurchaseInvoice, PurchaseInvoiceItem.invoice_id == PurchaseInvoice.id)
            .outerjoin(ItemUnit, PurchaseInvoiceItem.unit_id == ItemUnit.id)
            .filter(PurchaseInvoice.received_date >= self._since)
            .group_by(PurchaseInvoiceItem.item_id, effective_location)
        )

        if location_ids:
            query = query.filter(effective_location.in_(location_ids))
        if item_ids:
            query = query.filter(PurchaseInvoiceItem.item_id.in_(item_ids))

        return query

    def _open_po_totals(
        self, item_ids: Optional[Sequence[int]]
    ) -> Iterable[Tuple[int, float]]:
        factor = _coalesce_factor(ItemUnit.factor)
        query = (
            self.session.query(
                PurchaseOrderItem.item_id,
                func.sum(PurchaseOrderItem.quantity * factor).label("quantity"),
            )
            .join(PurchaseOrder, PurchaseOrderItem.purchase_order_id == PurchaseOrder.id)
            .outerjoin(ItemUnit, PurchaseOrderItem.unit_id == ItemUnit.id)
            .filter(PurchaseOrder.received.is_(False))
            .group_by(PurchaseOrderItem.item_id)
        )
        if item_ids:
            query = query.filter(PurchaseOrderItem.item_id.in_(item_ids))
        return query

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def build_recommendations(
        self,
        *,
        location_ids: Optional[Sequence[int]] = None,
        item_ids: Optional[Sequence[int]] = None,
        attendance_multiplier: float = 1.0,
        weather_multiplier: float = 1.0,
        promo_multiplier: float = 1.0,
        purchase_gl_code_ids: Optional[Sequence[int]] = None,
    ) -> List[ForecastRecommendation]:
        """Return forecast recommendations for the supplied filters."""

        data: Dict[Tuple[int, int], Dict[str, float]] = {}

        def entry_for(key: Tuple[int, int]) -> Dict[str, float]:
            record = data.setdefault(
                key,
                {
                    "sales_qty": 0.0,
                    "transfer_in_qty": 0.0,
                    "transfer_out_qty": 0.0,
                    "invoice_qty": 0.0,
                    "open_po_qty": 0.0,
                    "last_activity_ts": None,
                },
            )
            return record

        # Terminal sales
        for item_id, location_id, quantity, last_activity in self._terminal_sales_totals(
            location_ids, item_ids
        ):
            if item_id is None or location_id is None:
                continue
            entry = entry_for((item_id, location_id))
            entry["sales_qty"] += float(quantity or 0.0)
            if last_activity is not None:
                current = entry.get("last_activity_ts")
                if current is None or last_activity > current:
                    entry["last_activity_ts"] = last_activity

        # Transfers
        incoming, outgoing = self._transfer_totals(location_ids, item_ids)
        for (item_id, location_id), quantity in incoming:
            entry = entry_for((item_id, location_id))
            entry["transfer_in_qty"] += float(quantity)
        for (item_id, location_id), quantity in outgoing:
            entry = entry_for((item_id, location_id))
            entry["transfer_out_qty"] += float(quantity)

        # Purchase invoices
        for item_id, location_id, quantity in self._invoice_totals(location_ids, item_ids):
            if item_id is None or location_id is None:
                continue
            entry = entry_for((item_id, location_id))
            entry["invoice_qty"] += float(quantity or 0.0)

        # Open purchase orders (global by item)
        open_po_map: Dict[int, float] = {}
        for item_id, quantity in self._open_po_totals(item_ids):
            if item_id is None:
                continue
            open_po_map[item_id] = open_po_map.get(item_id, 0.0) + float(quantity or 0.0)

        for key in data.keys():
            item_id, _ = key
            if item_id in open_po_map:
                data[key]["open_po_qty"] = open_po_map[item_id]

        if not data:
            return []

        item_ids_needed = {item_id for item_id, _ in data.keys() if item_id is not None}
        location_ids_needed = {
            location_id for _, location_id in data.keys() if location_id is not None
        }

        items = {
            item.id: item
            for item in self.session.query(Item)
            .options(selectinload(Item.units))
            .filter(Item.id.in_(item_ids_needed))
        }
        locations = {
            location.id: location
            for location in self.session.query(Location)
            .filter(Location.id.in_(location_ids_needed))
        }

        multiplier = attendance_multiplier * weather_multiplier * promo_multiplier
        today = _dt.date.today()
        suggested_date = today + _dt.timedelta(days=self.lead_time_days)

        recommendations: List[ForecastRecommendation] = []
        for (item_id, location_id), history in data.items():
            item = items.get(item_id)
            location = locations.get(location_id)
            if item is None or location is None:
                continue

            if purchase_gl_code_ids:
                effective_code = item.purchase_gl_code_for_location(location.id)
                if (
                    effective_code is None
                    or effective_code.id not in purchase_gl_code_ids
                ):
                    continue

            base_consumption = history["sales_qty"] + history["transfer_out_qty"]
            adjusted_demand = base_consumption * multiplier
            incoming = (
                history["transfer_in_qty"] + history["invoice_qty"] + history["open_po_qty"]
            )
            recommended_quantity = max(adjusted_demand - incoming, 0.0)

            default_unit_id = None
            for unit in item.units:
                if unit.receiving_default:
                    default_unit_id = unit.id
                    break
            if default_unit_id is None and item.units:
                default_unit_id = item.units[0].id

            recommendations.append(
                ForecastRecommendation(
                    item=item,
                    location=location,
                    history=history,
                    base_consumption=base_consumption,
                    adjusted_demand=adjusted_demand,
                    recommended_quantity=recommended_quantity,
                    suggested_delivery_date=suggested_date,
                    default_unit_id=default_unit_id,
                )
            )

        recommendations.sort(
            key=lambda rec: (rec.recommended_quantity, rec.base_consumption),
            reverse=True,
        )
        return recommendations


__all__ = ["DemandForecastingHelper", "ForecastRecommendation"]

