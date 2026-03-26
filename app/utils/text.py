"""Text utility functions for InvoiceManager."""

from __future__ import annotations

import re
import unicodedata

_TRIM_WHITESPACE_RE = re.compile(r"^\s+|\s+$", flags=re.UNICODE)


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


__all__ = ["normalize_name_for_sorting"]
