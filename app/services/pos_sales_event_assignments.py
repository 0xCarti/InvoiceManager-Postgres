"""Resolve staged POS sales locations to open event locations."""

from __future__ import annotations

import json

from sqlalchemy.orm import selectinload

from app.models import (
    Event,
    EventLocation,
    PosSalesImport,
    PosSalesImportLocation,
)


def sales_import_location_is_skipped(
    import_location: PosSalesImportLocation,
) -> bool:
    """Return whether review metadata excludes an import location."""

    raw_metadata = import_location.approval_metadata
    if not raw_metadata:
        return False
    try:
        payload = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    review = payload.get("review")
    return bool(isinstance(review, dict) and review.get("skip"))


def load_sales_import_event_candidates(
    sales_import: PosSalesImport,
) -> dict[int, list[EventLocation]]:
    """Load open event-location candidates for an import's sales date."""

    candidate_lookup: dict[int, list[EventLocation]] = {}
    if sales_import.sales_date is None:
        return candidate_lookup

    location_ids = sorted(
        {
            import_location.location_id
            for import_location in sales_import.locations
            if import_location.location_id is not None
        }
    )
    if not location_ids:
        return candidate_lookup

    candidate_rows = (
        EventLocation.query.options(
            selectinload(EventLocation.event),
            selectinload(EventLocation.location),
        )
        .join(Event, Event.id == EventLocation.event_id)
        .filter(EventLocation.location_id.in_(location_ids))
        .filter(Event.closed.is_(False))
        .filter(Event.start_date <= sales_import.sales_date)
        .filter(Event.end_date >= sales_import.sales_date)
        .order_by(
            EventLocation.location_id.asc(),
            Event.start_date.asc(),
            Event.end_date.asc(),
            Event.id.asc(),
        )
        .all()
    )

    for candidate in candidate_rows:
        candidate_lookup.setdefault(candidate.location_id, []).append(
            candidate
        )
    return candidate_lookup


def resolve_sales_import_event_location(
    sales_import: PosSalesImport,
    import_location: PosSalesImportLocation,
    *,
    candidate_lookup: dict[int, list[EventLocation]] | None = None,
) -> EventLocation | None:
    """Return the one unambiguous event location for a staged location."""

    if (
        sales_import.status != PosSalesImport.STATUS_PENDING
        or sales_import.sales_date is None
        or import_location.location_id is None
        or sales_import_location_is_skipped(import_location)
    ):
        return None

    candidates = (
        candidate_lookup
        if candidate_lookup is not None
        else load_sales_import_event_candidates(sales_import)
    ).get(import_location.location_id, [])
    if len(candidates) != 1:
        return None
    return candidates[0]


def sync_sales_import_event_assignments(
    sales_import: PosSalesImport,
    *,
    candidate_lookup: dict[int, list[EventLocation]] | None = None,
) -> bool:
    """Synchronize persisted event assignments for a pending sales import."""

    if sales_import.status != PosSalesImport.STATUS_PENDING:
        return False

    candidate_lookup = (
        candidate_lookup
        if candidate_lookup is not None
        else load_sales_import_event_candidates(sales_import)
    )
    changed = False
    for import_location in sales_import.locations:
        matched_event_location = resolve_sales_import_event_location(
            sales_import,
            import_location,
            candidate_lookup=candidate_lookup,
        )
        candidate_id = (
            matched_event_location.id
            if matched_event_location is not None
            else None
        )
        if import_location.event_location_id != candidate_id:
            import_location.event_location_id = candidate_id
            changed = True
        if import_location.event_location is not matched_event_location:
            import_location.event_location = matched_event_location
    return changed
