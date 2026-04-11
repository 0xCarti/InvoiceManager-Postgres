"""Helpers for storing and validating user-configured dashboard cards."""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from app import db
from app.models import UserFilterPreference

DASHBOARD_METABASE_CARDS_SCOPE = "dashboard.metabase_cards"
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


def set_dashboard_metabase_card_visibility(
    user: Any,
    visible_card_ids: set[str],
) -> list[dict[str, Any]]:
    """Persist per-card dashboard visibility for ``user``."""

    cards = load_dashboard_metabase_cards(user)
    for card in cards:
        card["visible"] = card["id"] in visible_card_ids
    return save_dashboard_metabase_cards(user, cards)
