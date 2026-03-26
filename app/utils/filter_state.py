"""Helpers for storing and retrieving per-user filter defaults."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from werkzeug.datastructures import MultiDict

from app import db
from app.models import UserFilterPreference

FilterValues = dict[str, list[str]]


def _is_authenticated(user: Any) -> bool:
    """Return ``True`` when ``user`` is authenticated."""

    if user is None:
        return False
    return bool(getattr(user, "is_authenticated", False))


def normalize_filters(
    data: Mapping[str, Any] | MultiDict[str, str] | FilterValues,
    *,
    exclude: Sequence[str] | None = None,
) -> FilterValues:
    """Normalize raw filter parameters into a JSON-serializable structure.

    ``data`` may be a :class:`dict`, a :class:`MultiDict`, or a mapping of
    strings to iterables of strings.  The resulting dictionary always maps
    filter names to lists of strings which is convenient for storing in the
    session or database JSON column.
    """

    if exclude is None:
        exclude_set: set[str] = set()
    else:
        exclude_set = set(exclude)

    normalized: FilterValues = {}

    if isinstance(data, MultiDict):
        items = ((key, data.getlist(key)) for key in data.keys())
    elif isinstance(data, Mapping):
        items = data.items()
    else:
        raise TypeError("Unsupported data type for normalize_filters")

    for key, raw_values in items:
        if key in exclude_set:
            continue
        values: list[str] = []
        if isinstance(raw_values, (list, tuple, set)):
            iterable = raw_values
        else:
            iterable = [raw_values]
        for value in iterable:
            if value is None:
                continue
            values.append(str(value))
        if values:
            normalized[key] = values
    return normalized


def filters_to_query_args(values: FilterValues) -> dict[str, Any]:
    """Convert normalized filter values into arguments for ``url_for``."""

    query_args: dict[str, Any] = {}
    for key, entries in values.items():
        if not entries:
            continue
        if len(entries) == 1:
            query_args[key] = entries[0]
        else:
            query_args[key] = list(entries)
    return query_args


def get_filter_defaults(user: Any, scope: str) -> FilterValues:
    """Return stored defaults for ``scope`` belonging to ``user``."""

    if not _is_authenticated(user) or not scope:
        return {}
    preference = UserFilterPreference.query.filter_by(
        user_id=user.id, scope=scope
    ).one_or_none()
    if preference is None:
        return {}
    if not isinstance(preference.values, Mapping):
        return {}
    return normalize_filters(preference.values)


def set_filter_defaults(
    user: Any,
    scope: str,
    values: Mapping[str, Any] | MultiDict[str, str] | FilterValues,
    *,
    exclude: Sequence[str] | None = None,
) -> FilterValues:
    """Persist normalized defaults for ``scope`` belonging to ``user``."""

    if not _is_authenticated(user):
        raise ValueError("Cannot store filter defaults for anonymous users")
    normalized = normalize_filters(values, exclude=exclude)
    preference = UserFilterPreference.query.filter_by(
        user_id=user.id, scope=scope
    ).one_or_none()
    if normalized:
        if preference is None:
            preference = UserFilterPreference(user_id=user.id, scope=scope)
            db.session.add(preference)
        preference.values = normalized
    else:
        if preference is not None:
            db.session.delete(preference)
            preference = None
    db.session.commit()
    return normalized
