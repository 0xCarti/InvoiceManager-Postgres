"""Helpers for public location count submissions and manager review."""

from __future__ import annotations

from collections import defaultdict
from datetime import date as date_cls, datetime as datetime_cls, timedelta

from flask import current_app
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationOperatingDay,
    EventStandSheetItem,
    Item,
    Location,
    LocationCountSubmission,
    LocationCountSubmissionRow,
    LocationStandItem,
    Transfer,
    TransferItem,
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


def event_operating_dates(event: Event) -> list[date_cls]:
    """Return every calendar date in an event's inclusive date range."""

    if event.start_date is None or event.end_date is None:
        return []
    start_date = min(event.start_date, event.end_date)
    end_date = max(event.start_date, event.end_date)
    day_count = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(day_count + 1)]


def ensure_event_location_operating_days(
    event_location: EventLocation,
    open_dates: set[date_cls] | list[date_cls] | tuple[date_cls, ...] | None = None,
) -> list[EventLocationOperatingDay]:
    """Ensure an event location has rows for the dates it is open."""

    if event_location is None or event_location.event is None:
        return []

    valid_dates = set(event_operating_dates(event_location.event))
    if open_dates is None:
        desired_dates = valid_dates
    else:
        desired_dates = {
            date_value for date_value in open_dates if date_value in valid_dates
        }

    existing_by_date = {
        day.operating_date: day
        for day in EventLocationOperatingDay.query.filter_by(
            event_location_id=event_location.id
        ).all()
    }

    for operating_date in sorted(desired_dates):
        if operating_date in existing_by_date:
            continue
        day = EventLocationOperatingDay(
            event_location_id=event_location.id,
            operating_date=operating_date,
        )
        db.session.add(day)
        existing_by_date[operating_date] = day

    return [
        existing_by_date[operating_date]
        for operating_date in sorted(existing_by_date)
    ]


def ensure_event_operating_days(event: Event) -> None:
    """Backfill all-day operating rows for event locations that have none."""

    for event_location in event.locations or []:
        has_days = (
            EventLocationOperatingDay.query.filter_by(
                event_location_id=event_location.id
            ).first()
            is not None
        )
        if not has_days:
            ensure_event_location_operating_days(event_location)


def event_operating_day_for_submission(
    event_location: EventLocation | None,
    submission_date: date_cls | None,
    *,
    create_if_missing: bool = False,
) -> EventLocationOperatingDay | None:
    """Return the operating day row matching an event location and date."""

    if event_location is None or submission_date is None:
        return None

    day = EventLocationOperatingDay.query.filter_by(
        event_location_id=event_location.id,
        operating_date=submission_date,
    ).first()
    if day is not None:
        return day

    has_any_day = (
        EventLocationOperatingDay.query.filter_by(
            event_location_id=event_location.id
        ).first()
        is not None
    )
    if has_any_day or not create_if_missing:
        return None

    event = event_location.event
    if event is None or submission_date not in set(event_operating_dates(event)):
        return None

    ensure_event_location_operating_days(event_location)
    db.session.flush()
    return EventLocationOperatingDay.query.filter_by(
        event_location_id=event_location.id,
        operating_date=submission_date,
    ).first()


def load_open_event_location_candidates(
    target_date: date_cls, location_ids: list[int] | tuple[int, ...]
) -> dict[int, list[EventLocation]]:
    """Return open event-location matches for ``target_date`` keyed by location."""

    unique_location_ids = sorted(
        {location_id for location_id in location_ids if location_id}
    )
    if not unique_location_ids:
        return {}

    rows = (
        EventLocation.query.options(
            selectinload(EventLocation.event),
            selectinload(EventLocation.location),
            selectinload(EventLocation.operating_days),
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
        operating_days = row.operating_days or []
        if operating_days and not any(
            day.operating_date == target_date for day in operating_days
        ):
            continue
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
            selectinload(EventLocation.operating_days),
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
        operating_days = event_location.operating_days or []
        if operating_days:
            matches_date = any(
                day.operating_date == submission_date for day in operating_days
            )
        else:
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
            LocationCountSubmission.event_location_id == event_location_id,
            LocationCountSubmission.submission_date == submission_date,
        )
    else:
        query = query.filter(
            LocationCountSubmission.source_location_id == source_location_id,
            LocationCountSubmission.submission_date == submission_date,
        )

    return query.first() is not None


def aggregate_submission_rows_for_event_location_day(
    event_location_id: int,
    submission_type: str,
    operating_date: date_cls,
    *,
    approved_only: bool = True,
) -> dict[int, float]:
    """Return rolled-up submission rows for one event location on one date."""

    query = (
        LocationCountSubmission.query.options(
            selectinload(LocationCountSubmission.rows)
        )
        .filter(
            LocationCountSubmission.event_location_id == event_location_id,
            LocationCountSubmission.submission_type == submission_type,
            LocationCountSubmission.submission_date == operating_date,
        )
        .order_by(
            LocationCountSubmission.reviewed_at.asc(),
            LocationCountSubmission.submitted_at.asc(),
            LocationCountSubmission.id.asc(),
        )
    )
    if approved_only:
        query = query.filter(
            LocationCountSubmission.status == LocationCountSubmission.STATUS_APPROVED
        )
    return _roll_up_submission_rows(query.all())


def _event_location_transfer_totals_for_date(
    event_location: EventLocation,
    operating_date: date_cls,
) -> tuple[dict[int, float], dict[int, float]]:
    """Return completed transfer quantities into and out of a location on a date."""

    if event_location.location_id is None:
        return {}, {}

    transfers = (
        TransferItem.query.join(Transfer, Transfer.id == TransferItem.transfer_id)
        .filter(
            (Transfer.from_location_id == event_location.location_id)
            | (Transfer.to_location_id == event_location.location_id)
        )
        .all()
    )

    incoming: dict[int, float] = defaultdict(float)
    outgoing: dict[int, float] = defaultdict(float)
    for transfer_item in transfers:
        transfer_obj = transfer_item.transfer
        if transfer_obj is None or transfer_item.item_id is None:
            continue
        activity_date = (
            transfer_item.completed_at
            or transfer_obj.date_created
            or datetime_cls.utcnow()
        ).date()
        if activity_date != operating_date:
            continue
        quantity = float(transfer_item.completed_quantity or 0.0)
        if not quantity and transfer_obj.completed:
            quantity = float(transfer_item.quantity or 0.0)
        if not quantity:
            continue
        if transfer_obj.to_location_id == event_location.location_id:
            incoming[transfer_item.item_id] += quantity
        if transfer_obj.from_location_id == event_location.location_id:
            outgoing[transfer_item.item_id] += quantity
    return dict(incoming), dict(outgoing)


def expected_opening_counts_for_event_day(
    event_location: EventLocation,
    operating_date: date_cls,
) -> dict[int, float]:
    """Compute expected opening counts for one open event-location day."""

    previous_day = (
        EventLocationOperatingDay.query.filter(
            EventLocationOperatingDay.event_location_id == event_location.id,
            EventLocationOperatingDay.operating_date < operating_date,
        )
        .order_by(EventLocationOperatingDay.operating_date.desc())
        .first()
    )

    if previous_day is None:
        counts: dict[int, float] = {
            sheet.item_id: float(sheet.opening_count or 0.0)
            for sheet in event_location.stand_sheet_items or []
            if sheet.item_id is not None
        }
        if not counts and event_location.location_id is not None:
            for record in LocationStandItem.query.filter_by(
                location_id=event_location.location_id,
                countable=True,
            ).all():
                if record.item_id is not None:
                    counts[record.item_id] = float(record.expected_count or 0.0)
    else:
        counts = aggregate_submission_rows_for_event_location_day(
            event_location.id,
            LocationCountSubmission.TYPE_CLOSING,
            previous_day.operating_date,
        )

    incoming, outgoing = _event_location_transfer_totals_for_date(
        event_location,
        operating_date,
    )
    for item_id, quantity in incoming.items():
        counts[item_id] = counts.get(item_id, 0.0) + quantity
    for item_id, quantity in outgoing.items():
        counts[item_id] = counts.get(item_id, 0.0) - quantity
    return counts


def count_submission_type_uses_date_extreme(submission_type: str) -> str:
    """Return how approved submissions of ``submission_type`` should roll up."""

    if submission_type == LocationCountSubmission.TYPE_OPENING:
        return "earliest"
    if submission_type == LocationCountSubmission.TYPE_CLOSING:
        return "latest"
    return "all"


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


def _aggregate_submission_rows_for_type(
    event_location_id: int,
    submission_type: str,
) -> dict[int, float]:
    """Return item counts for the chosen approved submission date."""

    approved_submissions = (
        LocationCountSubmission.query.options(
            selectinload(LocationCountSubmission.rows).selectinload(
                LocationCountSubmissionRow.item
            )
        )
        .filter(
            LocationCountSubmission.event_location_id == event_location_id,
            LocationCountSubmission.status == LocationCountSubmission.STATUS_APPROVED,
            LocationCountSubmission.submission_type == submission_type,
        )
        .all()
    )
    if not approved_submissions:
        return {}

    approved_submissions.sort(
        key=lambda submission: (
            submission.submission_date,
            submission.reviewed_at or submission.submitted_at or datetime_cls.min,
            submission.id,
        )
    )

    date_extreme = count_submission_type_uses_date_extreme(submission_type)
    if date_extreme == "earliest":
        target_date = min(
            submission.submission_date for submission in approved_submissions
        )
        target_submissions = [
            submission
            for submission in approved_submissions
            if submission.submission_date == target_date
        ]
        return _roll_up_submission_rows(target_submissions)

    if date_extreme == "latest":
        target_date = max(
            submission.submission_date for submission in approved_submissions
        )
        target_submissions = [
            submission
            for submission in approved_submissions
            if submission.submission_date == target_date
        ]
        return _roll_up_submission_rows(target_submissions)

    totals_by_item_id: dict[int, float] = {}
    rows_by_date: dict[date_cls, list[LocationCountSubmission]] = defaultdict(list)
    for submission in approved_submissions:
        rows_by_date[submission.submission_date].append(submission)

    for submission_date in sorted(rows_by_date):
        daily_totals = _roll_up_submission_rows(rows_by_date[submission_date])
        for item_id, total_value in daily_totals.items():
            totals_by_item_id[item_id] = (
                totals_by_item_id.get(item_id, 0.0) + total_value
            )

    return totals_by_item_id


def _roll_up_submission_rows(
    submissions: list[LocationCountSubmission],
) -> dict[int, float]:
    """Combine submission rows honoring add vs overwrite approval modes."""

    totals_by_item_id: dict[int, float] = {}

    for submission in submissions:
        approval_mode = (
            submission.approval_mode
            or LocationCountSubmission.APPROVAL_MODE_ADD
        )
        for row in submission.rows:
            if row.item_id is None:
                continue
            row_value = float(row.count_value or 0.0)
            if approval_mode == LocationCountSubmission.APPROVAL_MODE_OVERWRITE:
                totals_by_item_id[row.item_id] = row_value
            else:
                totals_by_item_id[row.item_id] = (
                    totals_by_item_id.get(row.item_id, 0.0) + row_value
                )
    return totals_by_item_id


def sync_event_location_inventory_from_approved_submissions(
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

    totals_by_type = {
        submission_type: _aggregate_submission_rows_for_type(
            event_location_id,
            submission_type,
        )
        for submission_type in LocationCountSubmission.ALL_TYPES
    }
    opening_totals = totals_by_type[LocationCountSubmission.TYPE_OPENING]
    closing_totals = totals_by_type[LocationCountSubmission.TYPE_CLOSING]

    sheet_by_item_id = {
        sheet.item_id: sheet for sheet in (event_location.stand_sheet_items or [])
    }

    for sheet in sheet_by_item_id.values():
        sheet.opening_count = 0.0
        sheet.closing_count = 0.0
        sheet.eaten = 0.0
        sheet.spoiled = 0.0

    for submission_type, field_name in (
        (LocationCountSubmission.TYPE_OPENING, "opening_count"),
        (LocationCountSubmission.TYPE_CLOSING, "closing_count"),
        (LocationCountSubmission.TYPE_EATEN, "eaten"),
        (LocationCountSubmission.TYPE_SPOILAGE, "spoiled"),
    ):
        source_totals = totals_by_type[submission_type]
        if not source_totals:
            continue
        for item_id, total_count in source_totals.items():
            if item_id is None:
                continue
            sheet = sheet_by_item_id.get(item_id)
            if sheet is None:
                sheet = EventStandSheetItem(
                    event_location_id=event_location.id,
                    item_id=item_id,
                )
                db.session.add(sheet)
                sheet_by_item_id[item_id] = sheet
            setattr(sheet, field_name, total_count)

    if (
        closing_totals
        and event_location.location_id is not None
        and event_location.event is not None
        and event_location.event.closed
    ):
        for item_id, total_count in closing_totals.items():
            record = LocationStandItem.query.filter_by(
                location_id=event_location.location_id,
                item_id=item_id,
            ).first()
            item = db.session.get(Item, item_id)
            if record is None:
                record = LocationStandItem(
                    location_id=event_location.location_id,
                    item_id=item_id,
                    countable=True,
                    expected_count=0.0,
                    purchase_gl_code_id=(
                        item.purchase_gl_code_id if item is not None else None
                    ),
                )
                db.session.add(record)
            record.countable = True
            record.expected_count = total_count


def sync_event_location_counts_from_approved_submissions(
    event_location_id: int,
) -> None:
    """Backward-compatible wrapper for the broadened inventory sync."""

    sync_event_location_inventory_from_approved_submissions(event_location_id)
