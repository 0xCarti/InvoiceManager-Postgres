"""Text utility functions for InvoiceManager."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

_TRIM_WHITESPACE_RE = re.compile(r"^\s+|\s+$", flags=re.UNICODE)
DEFAULT_TEXT_MATCH_MODE = "contains"
TEXT_MATCH_MODES = {"exact", "startswith", "contains", "not_contains"}


def normalize_name_for_sorting(value: str | None) -> str:
    """Return a casefold-ready string for consistent alphabetical sorting.

    Leading and trailing whitespace is removed using a Unicode-aware regular
    expression so that non-breaking spaces (and similar characters) do not
    affect ordering.  The string is also normalised with NFKC to collapse
    compatibility characters into their canonical form.
    """

    if not value:
        return ""

    normalized = unicodedata.normalize("NFKC", value)
    return _TRIM_WHITESPACE_RE.sub("", normalized)




def normalize_request_text_filter(value: str | None) -> str:
    """Normalize optional free-text request filter values."""
    return (value or "").strip()

def normalize_text_match_mode(mode: str | None) -> str:
    """Normalize supported list/search text match modes."""
    normalized_mode = (mode or "").strip().lower()
    if normalized_mode in TEXT_MATCH_MODES:
        return normalized_mode
    return DEFAULT_TEXT_MATCH_MODE


def build_text_match_predicate(
    column: ColumnElement[Any],
    value: str,
    mode: str | None = None,
) -> ColumnElement[bool]:
    """Build a standardized case-insensitive text predicate for list filters."""
    match_mode = normalize_text_match_mode(mode)
    if match_mode == "exact":
        return func.lower(column) == value.lower()
    if match_mode == "startswith":
        return column.ilike(f"{value}%")
    if match_mode == "not_contains":
        return column.notilike(f"%{value}%")
    return column.ilike(f"%{value}%")


__all__ = [
    "DEFAULT_TEXT_MATCH_MODE",
    "TEXT_MATCH_MODES",
    "build_text_match_predicate",
    "normalize_name_for_sorting",
    "normalize_request_text_filter",
    "normalize_text_match_mode",
]
