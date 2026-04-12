"""Helpers for storing per-user bulletin shortcuts on the dashboard."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app import db
from app.models import UserFilterPreference

DASHBOARD_SAVED_BULLETINS_SCOPE = "dashboard.saved_bulletins"
MAX_SAVED_DASHBOARD_BULLETINS = 8


def _is_authenticated(user: Any) -> bool:
    if user is None:
        return False
    return bool(getattr(user, "is_authenticated", False))


def _stored_saved_bulletins_preference(user: Any) -> UserFilterPreference | None:
    if not _is_authenticated(user):
        return None
    return UserFilterPreference.query.filter_by(
        user_id=user.id,
        scope=DASHBOARD_SAVED_BULLETINS_SCOPE,
    ).one_or_none()


def _normalize_bulletin_ids(raw_values: list[Any]) -> list[int]:
    normalized_ids: list[int] = []
    seen_ids: set[int] = set()

    for raw_value in raw_values:
        try:
            communication_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if communication_id <= 0 or communication_id in seen_ids:
            continue
        normalized_ids.append(communication_id)
        seen_ids.add(communication_id)

    return normalized_ids


def load_saved_dashboard_bulletin_ids(user: Any) -> list[int]:
    """Return saved dashboard bulletin ids for ``user`` in stored order."""

    preference = _stored_saved_bulletins_preference(user)
    if preference is None or not isinstance(preference.values, Mapping):
        return []

    raw_ids = preference.values.get("communication_ids")
    if not isinstance(raw_ids, list):
        return []

    return _normalize_bulletin_ids(raw_ids)


def save_saved_dashboard_bulletin_ids(
    user: Any,
    communication_ids: list[int],
) -> list[int]:
    """Persist saved dashboard bulletin ids for ``user``."""

    if not _is_authenticated(user):
        raise ValueError("Cannot store dashboard bulletins for anonymous users.")

    normalized_ids = _normalize_bulletin_ids(communication_ids)
    if len(normalized_ids) > MAX_SAVED_DASHBOARD_BULLETINS:
        raise ValueError(
            f"You can save up to {MAX_SAVED_DASHBOARD_BULLETINS} bulletins on the dashboard."
        )

    preference = _stored_saved_bulletins_preference(user)
    if normalized_ids:
        if preference is None:
            preference = UserFilterPreference(
                user_id=user.id,
                scope=DASHBOARD_SAVED_BULLETINS_SCOPE,
            )
            db.session.add(preference)
        preference.values = {"communication_ids": normalized_ids}
    elif preference is not None:
        db.session.delete(preference)

    db.session.commit()
    return normalized_ids


def set_saved_dashboard_bulletin_state(
    user: Any,
    communication_id: int,
    *,
    saved: bool,
) -> list[int]:
    """Add or remove a bulletin from the user's saved dashboard shortcuts."""

    try:
        normalized_id = int(communication_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Choose a valid bulletin.") from exc

    if normalized_id <= 0:
        raise ValueError("Choose a valid bulletin.")

    existing_ids = load_saved_dashboard_bulletin_ids(user)
    updated_ids = [value for value in existing_ids if value != normalized_id]

    if saved:
        updated_ids.append(normalized_id)

    return save_saved_dashboard_bulletin_ids(user, updated_ids)
