"""Helpers for storing and validating user-configured dashboard cards."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from app import db
from app.models import UserFilterPreference

DASHBOARD_METABASE_CARDS_SCOPE = "dashboard.metabase_cards"
DASHBOARD_HIDDEN_SECTIONS_SCOPE = "dashboard.hidden_sections"
DASHBOARD_CARD_ORDER_SCOPE = "dashboard.card_order"
MAX_DASHBOARD_METABASE_CARDS = 12
DEFAULT_METABASE_CARD_HEIGHT = 420
MIN_METABASE_CARD_HEIGHT = 240
MAX_METABASE_CARD_HEIGHT = 1200
MAX_METABASE_CARD_TITLE_LENGTH = 120
MAX_METABASE_CARD_URL_LENGTH = 2000
ALLOWED_METABASE_PATH_PREFIXES = (
    "/public/",
    "/embed/",
    "/dashboard/",
    "/question/",
)

DASHBOARD_SECTION_DEFINITIONS: tuple[dict[str, str], ...] = (
    {
        "id": "transfers_summary",
        "label": "Transfers",
        "description": "The transfer summary card in the top row.",
    },
    {
        "id": "purchase_orders_summary",
        "label": "Purchase Orders",
        "description": "The purchase order summary card in the top row.",
    },
    {
        "id": "purchase_invoices_summary",
        "label": "Purchase Invoices",
        "description": "The purchase invoice summary card in the top row.",
    },
    {
        "id": "invoices_summary",
        "label": "Invoices",
        "description": "The sales invoice summary card in the top row.",
    },
    {
        "id": "transfer_completion",
        "label": "Transfer Completion",
        "description": "Transfer completion by location.",
    },
    {
        "id": "weekly_activity",
        "label": "Weekly Activity",
        "description": "Weekly transfers, purchases, and sales chart.",
    },
    {
        "id": "events_summary",
        "label": "Events",
        "description": "The events summary card.",
    },
    {
        "id": "action_queues",
        "label": "Action Queues",
        "description": "Pending purchase orders, transfer approvals, and invoice work queues.",
    },
    {
        "id": "event_schedule",
        "label": "Event Schedule",
        "description": "Calendar and scheduled events board.",
    },
    {
        "id": "bulletins",
        "label": "Bulletins",
        "description": "Pinned communications assigned to the current user.",
    },
)

DASHBOARD_SECTION_DEFINITIONS_BY_ID = {
    definition["id"]: definition for definition in DASHBOARD_SECTION_DEFINITIONS
}


def dashboard_section_card_key(section_id: str) -> str:
    """Return the persisted ordering key for a built-in dashboard section."""

    return f"section:{section_id}"


def dashboard_metabase_card_key(card_id: str) -> str:
    """Return the persisted ordering key for a saved Metabase dashboard card."""

    return f"metabase:{card_id}"


def _is_authenticated(user: Any) -> bool:
    if user is None:
        return False
    return bool(getattr(user, "is_authenticated", False))


def metabase_origin(site_url: str | None) -> str:
    """Return the normalized origin for the configured Metabase URL."""

    parsed = urlsplit((site_url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _coerce_card_id(raw_value: Any) -> str:
    card_id = str(raw_value or "").strip()
    if card_id:
        return card_id
    return secrets.token_hex(6)


def _coerce_card_height(raw_value: Any) -> int:
    try:
        height = int(raw_value)
    except (TypeError, ValueError):
        return DEFAULT_METABASE_CARD_HEIGHT
    return max(MIN_METABASE_CARD_HEIGHT, min(MAX_METABASE_CARD_HEIGHT, height))


def _coerce_card_visible(raw_value: Any) -> bool:
    if isinstance(raw_value, bool):
        return raw_value
    if raw_value is None:
        return True
    if isinstance(raw_value, str):
        normalized = raw_value.strip().lower()
        if normalized in {"0", "false", "off", "no"}:
            return False
        if normalized in {"1", "true", "on", "yes"}:
            return True
    return bool(raw_value)


def _stored_cards_preference(user: Any) -> UserFilterPreference | None:
    if not _is_authenticated(user):
        return None
    return UserFilterPreference.query.filter_by(
        user_id=user.id,
        scope=DASHBOARD_METABASE_CARDS_SCOPE,
    ).one_or_none()


def _stored_hidden_sections_preference(user: Any) -> UserFilterPreference | None:
    if not _is_authenticated(user):
        return None
    return UserFilterPreference.query.filter_by(
        user_id=user.id,
        scope=DASHBOARD_HIDDEN_SECTIONS_SCOPE,
    ).one_or_none()


def _stored_card_order_preference(user: Any) -> UserFilterPreference | None:
    if not _is_authenticated(user):
        return None
    return UserFilterPreference.query.filter_by(
        user_id=user.id,
        scope=DASHBOARD_CARD_ORDER_SCOPE,
    ).one_or_none()


def _unique_preserving_order(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    unique_values: list[str] = []

    for raw_value in values:
        normalized_value = str(raw_value or "").strip()
        if not normalized_value or normalized_value in seen:
            continue
        seen.add(normalized_value)
        unique_values.append(normalized_value)

    return unique_values


def load_dashboard_metabase_cards(user: Any) -> list[dict[str, Any]]:
    """Return stored dashboard cards for ``user`` without site validation."""

    preference = _stored_cards_preference(user)
    if preference is None or not isinstance(preference.values, Mapping):
        return []

    raw_cards = preference.values.get("cards")
    if not isinstance(raw_cards, list):
        return []

    cards: list[dict[str, Any]] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, Mapping):
            continue

        title = " ".join(str(raw_card.get("title") or "").split())
        embed_url = str(raw_card.get("embed_url") or "").strip()
        if not title or not embed_url:
            continue

        cards.append(
            {
                "id": _coerce_card_id(raw_card.get("id")),
                "title": title[:MAX_METABASE_CARD_TITLE_LENGTH],
                "embed_url": embed_url[:MAX_METABASE_CARD_URL_LENGTH],
                "height": _coerce_card_height(raw_card.get("height")),
                "visible": _coerce_card_visible(raw_card.get("visible")),
            }
        )

    return cards


def validate_metabase_card_input(
    *,
    title: Any,
    embed_url: Any,
    height: Any,
    metabase_site_url: str | None,
    card_id: str | None = None,
    visible: Any = True,
) -> dict[str, Any]:
    """Validate raw form input for a Metabase dashboard card."""

    normalized_title = " ".join(str(title or "").split())
    if not normalized_title:
        raise ValueError("Card title is required.")
    if len(normalized_title) > MAX_METABASE_CARD_TITLE_LENGTH:
        raise ValueError(
            f"Card title must be {MAX_METABASE_CARD_TITLE_LENGTH} characters or fewer."
        )

    normalized_url = str(embed_url or "").strip()
    if not normalized_url:
        raise ValueError("Metabase report link is required.")
    if len(normalized_url) > MAX_METABASE_CARD_URL_LENGTH:
        raise ValueError("Metabase report link is too long.")

    configured_origin = metabase_origin(metabase_site_url)
    if not configured_origin:
        raise ValueError("Metabase is not configured for this environment.")

    parsed = urlsplit(normalized_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            "Use a full Metabase URL that starts with http:// or https://."
        )

    if f"{parsed.scheme}://{parsed.netloc}" != configured_origin:
        raise ValueError(
            "Report links must use the configured Metabase site URL."
        )

    if not any(parsed.path.startswith(prefix) for prefix in ALLOWED_METABASE_PATH_PREFIXES):
        raise ValueError(
            "Use a Metabase public, embed, dashboard, or question link."
        )

    try:
        normalized_height = int(height)
    except (TypeError, ValueError) as exc:
        raise ValueError("Card height must be a whole number of pixels.") from exc

    if not MIN_METABASE_CARD_HEIGHT <= normalized_height <= MAX_METABASE_CARD_HEIGHT:
        raise ValueError(
            f"Card height must be between {MIN_METABASE_CARD_HEIGHT} and "
            f"{MAX_METABASE_CARD_HEIGHT} pixels."
        )

    return {
        "id": _coerce_card_id(card_id),
        "title": normalized_title,
        "embed_url": normalized_url,
        "height": normalized_height,
        "visible": _coerce_card_visible(visible),
    }


def cards_visible_on_dashboard(
    user: Any,
    *,
    metabase_site_url: str | None,
) -> list[dict[str, Any]]:
    """Return stored cards whose URLs match the configured Metabase origin."""

    configured_origin = metabase_origin(metabase_site_url)
    if not configured_origin:
        return []

    visible_cards: list[dict[str, Any]] = []
    for card in load_dashboard_metabase_cards(user):
        if not card.get("visible", True):
            continue
        parsed = urlsplit(card["embed_url"])
        if f"{parsed.scheme}://{parsed.netloc}" != configured_origin:
            continue
        if not any(parsed.path.startswith(prefix) for prefix in ALLOWED_METABASE_PATH_PREFIXES):
            continue
        visible_cards.append(card)
    return visible_cards


def load_dashboard_card_order(user: Any) -> list[str]:
    """Return the persisted dashboard card order keys for ``user``."""

    preference = _stored_card_order_preference(user)
    if preference is None or not isinstance(preference.values, Mapping):
        return []

    raw_card_keys = preference.values.get("card_keys")
    if not isinstance(raw_card_keys, list):
        return []

    return _unique_preserving_order(raw_card_keys)


def sort_dashboard_items(
    items: list[dict[str, Any]],
    order_keys: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return ``items`` sorted by persisted dashboard order keys."""

    order_positions = {
        order_key: position
        for position, order_key in enumerate(order_keys or [])
    }

    indexed_items = list(enumerate(items))
    indexed_items.sort(
        key=lambda pair: (
            order_positions.get(pair[1].get("order_key"), len(order_positions) + pair[0]),
            pair[0],
        )
    )
    return [item for _, item in indexed_items]


def save_dashboard_metabase_cards(
    user: Any,
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Persist dashboard cards for ``user``."""

    if not _is_authenticated(user):
        raise ValueError("Cannot store dashboard cards for anonymous users.")

    preference = _stored_cards_preference(user)
    if cards:
        if preference is None:
            preference = UserFilterPreference(
                user_id=user.id,
                scope=DASHBOARD_METABASE_CARDS_SCOPE,
            )
            db.session.add(preference)
        preference.values = {"cards": cards}
    elif preference is not None:
        db.session.delete(preference)

    db.session.commit()
    return cards


def save_dashboard_card_order(
    user: Any,
    card_order_keys: list[str],
) -> list[str]:
    """Persist the dashboard card order for ``user``."""

    if not _is_authenticated(user):
        raise ValueError("Cannot store dashboard card order for anonymous users.")

    normalized_order_keys = _unique_preserving_order(card_order_keys)
    preference = _stored_card_order_preference(user)
    if normalized_order_keys:
        if preference is None:
            preference = UserFilterPreference(
                user_id=user.id,
                scope=DASHBOARD_CARD_ORDER_SCOPE,
            )
            db.session.add(preference)
        preference.values = {"card_keys": normalized_order_keys}
    elif preference is not None:
        db.session.delete(preference)

    db.session.commit()
    return normalized_order_keys


def set_dashboard_metabase_card_visibility(
    user: Any,
    visible_card_ids: set[str],
) -> list[dict[str, Any]]:
    """Persist per-card dashboard visibility for ``user``."""

    cards = load_dashboard_metabase_cards(user)
    for card in cards:
        card["visible"] = card["id"] in visible_card_ids
    return save_dashboard_metabase_cards(user, cards)


def update_dashboard_card_order(
    user: Any,
    *,
    available_card_keys: list[str],
    ordered_card_keys: list[str],
) -> list[str]:
    """Update the persisted card order for the dashboard items visible in settings."""

    normalized_available_keys = _unique_preserving_order(available_card_keys)
    normalized_ordered_keys = [
        card_key
        for card_key in _unique_preserving_order(ordered_card_keys)
        if card_key in normalized_available_keys
    ]
    ordered_with_remainder = normalized_ordered_keys + [
        card_key
        for card_key in normalized_available_keys
        if card_key not in normalized_ordered_keys
    ]
    unavailable_existing_keys = [
        card_key
        for card_key in load_dashboard_card_order(user)
        if card_key not in normalized_available_keys
    ]

    return save_dashboard_card_order(
        user,
        ordered_with_remainder + unavailable_existing_keys,
    )


def load_hidden_dashboard_sections(user: Any) -> set[str]:
    """Return the per-user dashboard section ids hidden by the user."""

    preference = _stored_hidden_sections_preference(user)
    if preference is None or not isinstance(preference.values, Mapping):
        return set()

    raw_hidden = preference.values.get("section_ids")
    if not isinstance(raw_hidden, list):
        return set()

    valid_ids = set(DASHBOARD_SECTION_DEFINITIONS_BY_ID)
    return {
        str(section_id).strip()
        for section_id in raw_hidden
        if str(section_id).strip() in valid_ids
    }


def save_hidden_dashboard_sections(
    user: Any,
    hidden_section_ids: set[str],
) -> set[str]:
    """Persist the hidden built-in dashboard sections for ``user``."""

    if not _is_authenticated(user):
        raise ValueError("Cannot store dashboard sections for anonymous users.")

    valid_ids = set(DASHBOARD_SECTION_DEFINITIONS_BY_ID)
    normalized_hidden = {section_id for section_id in hidden_section_ids if section_id in valid_ids}

    preference = _stored_hidden_sections_preference(user)
    if normalized_hidden:
        if preference is None:
            preference = UserFilterPreference(
                user_id=user.id,
                scope=DASHBOARD_HIDDEN_SECTIONS_SCOPE,
            )
            db.session.add(preference)
        preference.values = {"section_ids": sorted(normalized_hidden)}
    elif preference is not None:
        db.session.delete(preference)

    db.session.commit()
    return normalized_hidden


def update_dashboard_section_visibility(
    user: Any,
    *,
    available_section_ids: set[str],
    visible_section_ids: set[str],
) -> set[str]:
    """Update hidden sections for the subset of cards currently shown in settings."""

    existing_hidden = load_hidden_dashboard_sections(user)
    normalized_available = {
        section_id
        for section_id in available_section_ids
        if section_id in DASHBOARD_SECTION_DEFINITIONS_BY_ID
    }
    normalized_visible = {
        section_id
        for section_id in visible_section_ids
        if section_id in normalized_available
    }

    updated_hidden = (existing_hidden - normalized_available) | (
        normalized_available - normalized_visible
    )
    return save_hidden_dashboard_sections(user, updated_hidden)
