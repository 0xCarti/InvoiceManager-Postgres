"""Timezone helpers for app-wide business date calculations."""

from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from flask import current_app


@lru_cache(maxsize=1)
def _timezone_lookup() -> dict[str, str]:
    return {
        timezone_name.casefold(): timezone_name
        for timezone_name in available_timezones()
    }


def normalize_timezone_name(value: str | None, default: str = "UTC") -> str:
    """Return a valid IANA timezone name, accepting case-only mismatches."""

    raw_value = (value or "").strip()
    if not raw_value:
        return default

    try:
        ZoneInfo(raw_value)
        return raw_value
    except Exception:
        pass

    if "/" in raw_value:
        region, remainder = raw_value.split("/", 1)
        if region.casefold() == "american":
            raw_value = f"America/{remainder}"

    matched_timezone = _timezone_lookup().get(raw_value.casefold())
    if matched_timezone:
        return matched_timezone
    return default


def get_timezone(value: str | None, default: str = "UTC") -> ZoneInfo:
    return ZoneInfo(normalize_timezone_name(value, default=default))


def get_default_timezone_name(default: str = "UTC") -> str:
    timezone_name = None
    try:
        timezone_name = current_app.config.get("DEFAULT_TIMEZONE")
    except RuntimeError:
        timezone_name = None

    if not timezone_name:
        import app as app_module

        timezone_name = getattr(app_module, "DEFAULT_TIMEZONE", None)

    return normalize_timezone_name(timezone_name, default=default)


def get_default_timezone(default: str = "UTC") -> ZoneInfo:
    return get_timezone(get_default_timezone_name(default=default), default=default)


def utc_now() -> datetime:
    return datetime.now(dt_timezone.utc)


def default_timezone_date(reference_time: datetime | None = None) -> date:
    """Return the configured app timezone date for a UTC reference time."""

    if reference_time is None:
        reference_time = utc_now()
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=dt_timezone.utc)
    return reference_time.astimezone(get_default_timezone()).date()
