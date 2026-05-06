"""Helpers for public location count submissions and manager review."""

from __future__ import annotations

from datetime import date as date_cls

from flask import current_app
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    Event,
    EventLocation,
    EventStandSheetItem,
    Location,
    LocationCountSubmission,
    LocationCountSubmissionRow,
    LocationStandItem,
)
from app.utils.menu_assignments import (
    get_authoritative_location_products,
    get_location_drift_recipe_item_ids,
)
from app.utils.text import normalize_name_for_sorting
from app.utils.units import (
    DEFAULT_BASE_UNIT_CONVERSIONS,
    convert_quantity_for_reporting,
    convert_report_value_to_base,
    get_unit_label,
)


def _conversion_mapping() -> dict[str, str]:
    configured = current_app.config.get("BASE_UNIT_CONVERSIONS") or {}
    conversions = dict(DEFAULT_BASE_UNIT_CONVERSIONS)
    conversions.update(configured)
    return conversions


def load_open_event_location_candidates(
    target_date: date_cls, location_ids: list[int] | tuple[int, ...]
) -> dict[int, list[EventLocation]]:
    """Return open event-location matches for ``target_date`` keyed by location."""

    unique_location_ids = sorted({location_id for location_id in location_ids if location_id})
    if not unique_location_ids:
        return {}

    rows = (
        EventLocation.query.options(
            selectinload(EventLocation.event),
            selectinload(EventLocation.location),
        )
        .join(Event, Event.id == EventLocation.event_id)
        .filter(EventLocation.location_id.in_(unique_location_ids))
        .filter(Event.closed.is_(False))
        .filter(Event.start_date <= target_date)
        .filter(Event.end_date >= target_date)
        .order_by(
            EventLocation.location_id.asc(),
            Event.start_date.asc(),
            Event.end_date.asc(),
            Event.id.asc(),
        )
        .all()
    )

    lookup: dict[int, list[EventLocation]] = {}
    for row in rows:
        lookup.setdefault(row.location_id, []).append(row)
    return lookup


def choose_auto_matched_event_location(
    location_id: int | None, target_date: date_cls
) -> EventLocation | None:
    """Return the single open event-location match for ``location_id`` on ``target_date``."""

    if not location_id:
        return None
    candidates = load_open_event_location_candidates(target_date, [location_id]).get(
        location_id, []
    )
    if len(candidates) != 1:
        return None
    return candidates[0]


def list_event_location_candidates(
    location_id: int | None,
    *,
    submission_date: date_cls | None = None,
) -> list[EventLocation]:
    """Return review candidates for a mapped location."""

    if not location_id:
        return []

    rows = (
        EventLocation.query.options(
            selectinload(EventLocation.event),
            selectinload(EventLocation.location),
        )
        .join(Event, Event.id == EventLocation.event_id)
        .filter(EventLocation.location_id == location_id)
        .order_by(Event.end_date.desc(), Event.start_date.desc(), Event.id.desc())
        .limit(30)
        .all()
    )

    if submission_date is None:
        return rows

    def sort_key(event_location: EventLocation) -> tuple[int, int, int, int]:
        event = event_location.event
        if event is None:
            return (3, 0, 0, 0)
        matches_date = event.start_date <= submission_date <= event.end_date
        closed_rank = 1 if event.closed else 0
        distance = min(
            abs((event.start_date - submission_date).days),
            abs((event.end_date - submission_date).days),
        )
        return (
            0 if matches_date else 1,
            closed_rank,
            distance,
            -event.id,
        )

    return sorted(rows, key=sort_key)


def opening_submission_exists(
    source_location_id: int,
    submission_date: date_cls,
    *,
    event_location_id: int | None = None,
) -> bool:
    """Return whether an opening submission already exists for the given context."""

    query = LocationCountSubmission.query.filter(
        LocationCountSubmission.status.in_(
            [
                LocationCountSubmission.STATUS_PENDING,
                LocationCountSubmission.STATUS_APPROVED,
            ]
        )
    ).filter(
        LocationCountSubmission.submission_type
        == LocationCountSubmission.TYPE_OPENING
    )

    if event_location_id is not None:
        query = query.filter(
            LocationCountSubmission.event_location_id == event_location_id
        )
    else:
        query = query.filter(
            LocationCountSubmission.source_location_id == source_location_id,
            LocationCountSubmission.submission_date == submission_date,
        )

    return query.first() is not None


def build_location_count_item_entries(location: Location) -> list[dict]:
    """Return countable item entries for a location's mobile count sheet."""

    conversions = _conversion_mapping()
    stand_records = LocationStandItem.query.filter_by(location_id=location.id).all()
    stand_by_item_id = {record.item_id: record for record in stand_records}
    drift_item_ids = get_location_drift_recipe_item_ids(location)

    entries: list[dict] = []
    seen: set[int] = set()

    for product_obj in get_authoritative_location_products(location):
        for recipe_item in product_obj.recipe_items:
            if recipe_item.item_id in seen or recipe_item.item is None:
                continue
            record = stand_by_item_id.get(recipe_item.item_id)
            is_countable = (
                record.countable if record is not None else recipe_item.countable
            )
            if not is_countable:
                continue
            item = recipe_item.item
            base_unit = item.base_unit
            report_unit = conversions.get(base_unit, base_unit) if base_unit else base_unit
            entries.append(
                {
                    "item": item,
                    "base_unit": base_unit,
                    "report_unit": report_unit,
                    "report_unit_label": get_unit_label(report_unit),
                }
            )
            seen.add(recipe_item.item_id)

    for record in stand_records:
        if record.item_id in seen or not record.countable:
            continue
        if record.item_id in drift_item_ids:
            continue
        item = record.item
        if item is None:
            continue
        base_unit = item.base_unit
        report_unit = conversions.get(base_unit, base_unit) if base_unit else base_unit
        entries.append(
            {
                "item": item,
                "base_unit": base_unit,
                "report_unit": report_unit,
                "report_unit_label": get_unit_label(report_unit),
            }
        )
        seen.add(record.item_id)

    entries.sort(
        key=lambda entry: normalize_name_for_sorting(entry["item"].name).casefold()
    )
    return entries


def build_submission_row_entries(submission: LocationCountSubmission) -> list[dict]:
    """Return review entries for a submission's count rows."""

    location_obj = submission.location or submission.source_location
    metadata_by_item_id: dict[int, dict] = {}
    if location_obj is not None:
        metadata_by_item_id = {
            entry["item"].id: entry for entry in build_location_count_item_entries(location_obj)
        }

    entries: list[dict] = []
    for row in submission.rows:
        item = row.item
        if item is None:
            continue
        metadata = metadata_by_item_id.get(item.id, {})
        base_unit = metadata.get("base_unit") or item.base_unit
        report_unit = metadata.get("report_unit") or base_unit
        display_value, _ = convert_quantity_for_reporting(
            float(row.count_value or 0.0),
            base_unit,
            _conversion_mapping(),
        )
        entries.append(
            {
                "row": row,
                "item": item,
                "base_unit": base_unit,
                "report_unit": report_unit,
                "report_unit_label": metadata.get("report_unit_label")
                or get_unit_label(report_unit),
                "display_value": display_value,
            }
        )
    return entries


def parse_submission_count_value(
    raw_value: float | str | None,
    *,
    base_unit: str | None,
    report_unit: str | None,
) -> float:
    """Normalize a submitted review/mobile count into the item's base unit."""

    try:
        numeric_value = float(str(raw_value or "").strip() or 0.0)
    except (TypeError, ValueError):
        numeric_value = 0.0
    return float(
        convert_report_value_to_base(numeric_value, base_unit, report_unit)
    )


def sync_event_location_counts_from_approved_submissions(
    event_location_id: int,
) -> None:
    """Apply approved mobile submissions onto the event stand sheet."""

    event_location = (
        EventLocation.query.options(
            selectinload(EventLocation.event),
            selectinload(EventLocation.location),
            selectinload(EventLocation.stand_sheet_items),
        )
        .filter(EventLocation.id == event_location_id)
        .first()
    )
    if event_location is None:
        return

    approved_opening = (
        LocationCountSubmission.query.options(
            selectinload(LocationCountSubmission.rows).selectinload(
                LocationCountSubmissionRow.item
            )
        )
        .filter(
            LocationCountSubmission.event_location_id == event_location_id,
            LocationCountSubmission.status == LocationCountSubmission.STATUS_APPROVED,
            LocationCountSubmission.submission_type
            == LocationCountSubmission.TYPE_OPENING,
        )
        .order_by(
            LocationCountSubmission.submission_date.asc(),
            LocationCountSubmission.submitted_at.asc(),
            LocationCountSubmission.id.asc(),
        )
        .first()
    )
    approved_closing = (
        LocationCountSubmission.query.options(
            selectinload(LocationCountSubmission.rows).selectinload(
                LocationCountSubmissionRow.item
            )
        )
        .filter(
            LocationCountSubmission.event_location_id == event_location_id,
            LocationCountSubmission.status == LocationCountSubmission.STATUS_APPROVED,
            LocationCountSubmission.submission_type
            == LocationCountSubmission.TYPE_CLOSING,
        )
        .order_by(
            LocationCountSubmission.submission_date.desc(),
            LocationCountSubmission.submitted_at.desc(),
            LocationCountSubmission.id.desc(),
        )
        .first()
    )

    sheet_by_item_id = {
        sheet.item_id: sheet for sheet in (event_location.stand_sheet_items or [])
    }

    for sheet in sheet_by_item_id.values():
        sheet.opening_count = 0.0
        sheet.closing_count = 0.0

    for source, field_name in (
        (approved_opening, "opening_count"),
        (approved_closing, "closing_count"),
    ):
        if source is None:
            continue
        for row in source.rows:
            if row.item_id is None:
                continue
            sheet = sheet_by_item_id.get(row.item_id)
            if sheet is None:
                sheet = EventStandSheetItem(
                    event_location_id=event_location.id,
                    item_id=row.item_id,
                )
                db.session.add(sheet)
                sheet_by_item_id[row.item_id] = sheet
            setattr(sheet, field_name, float(row.count_value or 0.0))

    if (
        approved_closing is not None
        and event_location.location_id is not None
        and event_location.event is not None
        and event_location.event.closed
    ):
        for row in approved_closing.rows:
            record = LocationStandItem.query.filter_by(
                location_id=event_location.location_id,
                item_id=row.item_id,
            ).first()
            if record is None:
                record = LocationStandItem(
                    location_id=event_location.location_id,
                    item_id=row.item_id,
                    countable=True,
                    expected_count=0.0,
                    purchase_gl_code_id=(
                        row.item.purchase_gl_code_id if row.item is not None else None
                    ),
                )
                db.session.add(record)
            record.countable = True
            record.expected_count = float(row.count_value or 0.0)
