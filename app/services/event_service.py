"""Helpers for retrieving event data for dashboard widgets."""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from typing import Iterable, List
from zoneinfo import ZoneInfo

from flask import current_app
from flask_login import current_user
from app.models import Event


@dataclass
class CalendarDay:
    """Representation of a single calendar day and the events on it."""

    date: date
    count: int
    events: List[dict]

    @property
    def day(self) -> int:
        return self.date.day


def _utcnow() -> datetime:
    """Return the current UTC datetime for computing local dates."""

    return datetime.now(dt_timezone.utc)


def current_user_today(today: date | None = None) -> date:
    """Return today's date localized to the current user's time zone."""

    if today is not None:
        return today

    tz_name = getattr(current_user, "timezone", None)
    if not tz_name:
        tz_name = current_app.config.get("DEFAULT_TIMEZONE") if current_app else None
    if not tz_name:
        import app as app_module

        tz_name = getattr(app_module, "DEFAULT_TIMEZONE", None)
    tz_name = tz_name or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    return _utcnow().astimezone(tz).date()


def _event_status(event: Event, today: date) -> str:
    """Return a label describing the event's status relative to ``today``."""

    if event.end_date < today:
        return "past_due"
    if event.start_date > today:
        return "upcoming"
    if event.start_date == today:
        return "active"
    return "in_progress"


def _calendar_days(
    events: Iterable[Event], today: date, open_location_counts: dict[int, int]
) -> List[CalendarDay]:
    """Return calendar day data for the month that contains ``today``."""

    month_start = date(today.year, today.month, 1)
    _, days_in_month = monthrange(today.year, today.month)

    calendar_days: List[CalendarDay] = []
    for offset in range(days_in_month):
        current_day = month_start + timedelta(days=offset)
        day_events = [
            {
                "name": event.name,
                "open_location_count": open_location_counts.get(event.id, 0),
            }
            for event in events
            if event.start_date <= current_day <= event.end_date
        ]
        calendar_days.append(
            CalendarDay(date=current_day, count=len(day_events), events=day_events)
        )

    return calendar_days


def event_schedule(today: date | None = None) -> dict:
    """Return upcoming/active events and calendar data for the dashboard."""

    today = current_user_today(today)

    open_events = (
        Event.query.filter(Event.closed.is_(False))
        .order_by(Event.start_date.asc(), Event.end_date.asc())
        .all()
    )

    open_location_counts = {
        event.id: sum(1 for loc in event.locations if not loc.confirmed)
        for event in open_events
    }

    calendar_days = _calendar_days(open_events, today, open_location_counts)

    events = [
        {
            "id": event.id,
            "name": event.name,
            "open_location_count": open_location_counts.get(event.id, 0),
            "start_date": event.start_date,
            "end_date": event.end_date,
            "status": _event_status(event, today),
        }
        for event in open_events
    ]

    return {
        "events": events,
        "sidebar_events": events,
        "calendar": {
            "month_label": today.strftime("%B %Y"),
            "days": calendar_days,
            "today": today,
        },
    }
