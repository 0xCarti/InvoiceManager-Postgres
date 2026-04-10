"""Helper functions for collecting dashboard metrics."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func

from app import db
from app.models import (
    Event,
    Invoice,
    Location,
    PurchaseInvoice,
    PurchaseOrder,
    Transfer,
    TransferItem,
)
from app.services.event_service import current_user_today, event_schedule


def _coalesce_scalar(query) -> float:
    """Return a numeric scalar result or ``0.0`` when ``None``."""

    result = query.scalar()
    return float(result or 0.0)


def transfer_summary() -> Dict[str, int]:
    """Return counts for transfers used on the dashboard."""

    total = _coalesce_scalar(db.session.query(func.count(Transfer.id)))
    completed = _coalesce_scalar(
        db.session.query(func.count(Transfer.id)).filter(Transfer.completed.is_(True))
    )
    pending = int(total - completed)

    return {
        "total": int(total),
        "completed": int(completed),
        "pending": pending,
    }


def transfer_completion_by_location() -> List[Dict[str, Any]]:
    """Return open-transfer completion ratios grouped by destination location."""

    rows = (
        db.session.query(
            Transfer.to_location_id.label("location_id"),
            Location.name.label("location_name"),
            func.count(func.distinct(Transfer.id)).label("transfer_count"),
            func.sum(TransferItem.quantity).label("total_quantity"),
            func.sum(TransferItem.completed_quantity).label("completed_quantity"),
        )
        .join(TransferItem, TransferItem.transfer_id == Transfer.id)
        .join(Location, Location.id == Transfer.to_location_id)
        .filter(Transfer.completed.is_(False))
        .group_by(Transfer.to_location_id, Location.name)
        .order_by(Location.name.asc())
    )

    results: List[Dict[str, Any]] = []
    for row in rows:
        total_qty = float(row.total_quantity or 0.0)
        completed_qty = float(row.completed_quantity or 0.0)
        completion_percent = (completed_qty / total_qty * 100.0) if total_qty else 0.0
        results.append(
            {
                "location_id": row.location_id,
                "location_name": row.location_name,
                "transfer_count": int(row.transfer_count or 0),
                "completion_percent": completion_percent,
            }
        )

    return results


def purchase_order_summary(today: Optional[date] = None) -> Dict[str, Any]:
    """Return open counts and totals for purchase orders."""

    today = today or date.today()
    open_orders = PurchaseOrder.query.filter(PurchaseOrder.received.is_(False))
    overdue_orders = open_orders.filter(PurchaseOrder.expected_date < today)

    return {
        "open_count": open_orders.count(),
        "overdue_count": overdue_orders.count(),
        "expected_total": _coalesce_scalar(
            db.session.query(func.sum(PurchaseOrder.expected_total_cost)).filter(
                PurchaseOrder.received.is_(False)
            )
        ),
    }


def purchase_invoice_summary() -> Dict[str, Any]:
    """Return totals for received purchase invoices."""

    invoices = PurchaseInvoice.query.all()
    total = sum(invoice.total for invoice in invoices)

    return {
        "count": len(invoices),
        "total": float(total),
    }


def invoices_pending_posting(limit: int = 5) -> Dict[str, Any]:
    """Return recently received purchase invoices that need posting/payment."""

    query = PurchaseInvoice.query.order_by(PurchaseInvoice.received_date.desc())
    total = query.count()

    return {
        "items": query.limit(limit).all(),
        "total": total,
    }


def invoice_summary() -> Dict[str, Any]:
    """Return counts and totals for customer invoices."""

    invoices = Invoice.query.all()
    total = sum(invoice.total for invoice in invoices)

    return {
        "count": len(invoices),
        "total": float(total),
    }


def pending_purchase_orders(limit: int = 5) -> Dict[str, Any]:
    """Return open purchase orders awaiting receipt."""

    query = PurchaseOrder.query.filter(PurchaseOrder.received.is_(False)).order_by(
        PurchaseOrder.expected_date.asc(), PurchaseOrder.order_date.asc()
    )
    total = query.count()

    return {
        "items": query.limit(limit).all(),
        "total": total,
    }


def pending_transfers(limit: int = 5) -> Dict[str, Any]:
    """Return transfers that still need approval/completion."""

    query = Transfer.query.filter(Transfer.completed.is_(False)).order_by(
        Transfer.date_created.desc()
    )
    total = query.count()

    return {
        "items": query.limit(limit).all(),
        "total": total,
    }


def event_summary(today: Optional[date] = None) -> Dict[str, Any]:
    """Return today's and upcoming event counts for dashboard widgets."""

    today = current_user_today(today)
    next_day = today + timedelta(days=1)

    todays_events = Event.query.filter(
        Event.closed.is_(False),
        Event.start_date <= today,
        Event.end_date >= today,
    )
    upcoming_events = Event.query.filter(
        Event.closed.is_(False),
        Event.start_date > today,
    )
    next_event = upcoming_events.order_by(Event.start_date.asc()).first()
    next_day_events = (
        Event.query.filter(
            Event.closed.is_(False),
            Event.start_date == next_day,
        )
        .order_by(Event.start_date.asc(), Event.name.asc())
        .all()
    )

    return {
        "today_count": todays_events.count(),
        "upcoming_count": upcoming_events.count(),
        "next_event": next_event,
        "next_day": next_day,
        "next_day_events": next_day_events,
    }


def _resolve_activity_interval(value: Optional[str]) -> Tuple[str, str]:
    """Return canonical/internal interval and selected UI value."""

    selected_value = (value or "weekly").strip().lower()
    allowed_intervals = {
        "weekly": "week",
        "month": "month",
        "quarter": "quarter",
        "half_year": "half_year",
        "year": "year",
    }
    internal_interval = allowed_intervals.get(selected_value, "week")
    if selected_value not in allowed_intervals:
        selected_value = "weekly"
    return internal_interval, selected_value


def dashboard_context(activity_interval: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate metrics for the dashboard view."""

    today = current_user_today()

    events = event_summary(today)
    events["schedule"] = event_schedule(today)

    resolved_interval, selected_interval = _resolve_activity_interval(activity_interval)
    weekly_activity = weekly_transfer_purchase_activity(
        today=today,
        interval=resolved_interval,
    )
    weekly_activity["selected_interval"] = selected_interval
    weekly_activity["interval_options"] = [
        {"value": "weekly", "label": "Weekly"},
        {"value": "month", "label": "Monthly"},
        {"value": "quarter", "label": "Quarterly"},
        {"value": "half_year", "label": "Half-year"},
        {"value": "year", "label": "Yearly"},
    ]

    return {
        "transfers": transfer_summary(),
        "transfer_completion_by_location": transfer_completion_by_location(),
        "purchase_orders": purchase_order_summary(today),
        "purchase_invoices": purchase_invoice_summary(),
        "invoices": invoice_summary(),
        "events": events,
        "charts": {
            "weekly_activity": weekly_activity,
        },
        "queues": {
            "purchase_orders": pending_purchase_orders(),
            "transfers": pending_transfers(),
            "purchase_invoices": invoices_pending_posting(),
        },
    }


def _interval_start(value: date, interval: str) -> date:
    if interval == "week":
        return value - timedelta(days=value.weekday())
    if interval == "month":
        return value.replace(day=1)
    if interval == "quarter":
        quarter_month = ((value.month - 1) // 3) * 3 + 1
        return value.replace(month=quarter_month, day=1)
    if interval == "half_year":
        half_start_month = 1 if value.month <= 6 else 7
        return value.replace(month=half_start_month, day=1)
    if interval == "year":
        return value.replace(month=1, day=1)
    raise ValueError(f"Unsupported interval: {interval}")


def _add_interval(start: date, interval: str, count: int = 1) -> date:
    if interval == "week":
        return start + timedelta(weeks=count)

    if interval == "month":
        total_months = start.month - 1 + count
        year = start.year + (total_months // 12)
        month = total_months % 12 + 1
        return start.replace(year=year, month=month, day=1)

    if interval == "quarter":
        return _add_interval(start, "month", count * 3)

    if interval == "half_year":
        return _add_interval(start, "month", count * 6)

    if interval == "year":
        return start.replace(year=start.year + count, month=1, day=1)

    raise ValueError(f"Unsupported interval: {interval}")


def weekly_transfer_purchase_activity(
    weeks: int = 6,
    today: Optional[date] = None,
    interval: str = "week",
    periods: Optional[int] = None,
) -> Dict[str, Any]:
    """Return interval buckets for transfer, purchase, and sales activity."""

    today = today or date.today()

    bucket_count = periods if periods is not None else weeks
    current_interval_start = _interval_start(today, interval)
    start_week = _add_interval(current_interval_start, interval, -(bucket_count - 1))
    week_starts = [_add_interval(start_week, interval, i) for i in range(bucket_count)]
    buckets = {
        start: {
            "week_start": start.isoformat(),
            "week_end": (_add_interval(start, interval) - timedelta(days=1)).isoformat(),
            "label": f"{start.strftime('%b %d')} – {(_add_interval(start, interval) - timedelta(days=1)).strftime('%b %d')}",
            "transfers": 0,
            "purchases": 0,
            "purchase_total": 0.0,
            "sales": 0,
            "sales_total": 0.0,
        }
        for start in week_starts
    }

    transfer_start_dt = datetime.combine(start_week, datetime.min.time())
    transfers = Transfer.query.filter(Transfer.date_created >= transfer_start_dt).all()

    for transfer in transfers:
        bucket_start = _interval_start(transfer.date_created.date(), interval)
        if bucket_start in buckets:
            buckets[bucket_start]["transfers"] += 1

    purchases = PurchaseInvoice.query.filter(
        PurchaseInvoice.received_date >= start_week
    ).all()

    for invoice in purchases:
        bucket_start = _interval_start(invoice.received_date, interval)
        if bucket_start in buckets:
            buckets[bucket_start]["purchases"] += 1
            buckets[bucket_start]["purchase_total"] += float(invoice.total)

    sales = Invoice.query.filter(Invoice.date_created >= transfer_start_dt).all()

    for sale in sales:
        bucket_start = _interval_start(sale.date_created.date(), interval)
        if bucket_start in buckets:
            buckets[bucket_start]["sales"] += 1
            buckets[bucket_start]["sales_total"] += float(sale.total)

    interval_bucket_label = "Period"
    interval_empty_state_text = (
        "No recent transfer, purchase, or sales activity for the selected period."
    )

    return {
        "interval": interval,
        "bucket_label": interval_bucket_label,
        "empty_state_text": interval_empty_state_text,
        "buckets": [buckets[start] for start in sorted(buckets.keys())],
    }
