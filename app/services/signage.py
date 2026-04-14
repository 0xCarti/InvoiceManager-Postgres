from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import selectinload

from app import db
from app.models import Display, Location, Menu, Playlist, PlaylistItem, Product

DISPLAY_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_display_token() -> str:
    while True:
        token = secrets.token_urlsafe(24)
        if not Display.query.filter_by(public_token=token).first():
            return token


def normalize_display_browser_code(raw_value: str | None) -> str:
    cleaned = "".join(
        ch for ch in (raw_value or "").upper() if ch in DISPLAY_CODE_ALPHABET
    )
    return cleaned[:8]


def generate_display_browser_code() -> str:
    while True:
        code = "".join(secrets.choice(DISPLAY_CODE_ALPHABET) for _ in range(6))
        if not Display.query.filter_by(browser_code=code).first():
            return code


def normalize_activation_code(raw_value: str | None) -> str:
    allowed = string.ascii_uppercase + string.digits
    cleaned = "".join(ch for ch in (raw_value or "").upper() if ch in allowed)
    return cleaned[:8]


def generate_display_activation_code() -> str:
    while True:
        code = "".join(secrets.choice(DISPLAY_CODE_ALPHABET) for _ in range(6))
        if not Display.query.filter_by(activation_code=code).first():
            return code


def load_display_for_player(public_token: str) -> Display | None:
    return (
        Display.query.options(
            selectinload(Display.location)
            .selectinload(Location.current_menu)
            .selectinload(Menu.products),
            selectinload(Display.location)
            .selectinload(Location.default_playlist)
            .selectinload(Playlist.items)
            .selectinload(PlaylistItem.menu)
            .selectinload(Menu.products),
            selectinload(Display.playlist_override)
            .selectinload(Playlist.items)
            .selectinload(PlaylistItem.menu)
            .selectinload(Menu.products),
        )
        .filter_by(public_token=public_token, archived=False)
        .first()
    )


def load_display_for_browser_code(browser_code: str) -> Display | None:
    normalized = normalize_display_browser_code(browser_code)
    if not normalized:
        return None
    return (
        Display.query.options(
            selectinload(Display.location)
            .selectinload(Location.current_menu)
            .selectinload(Menu.products),
            selectinload(Display.location)
            .selectinload(Location.default_playlist)
            .selectinload(Playlist.items)
            .selectinload(PlaylistItem.menu)
            .selectinload(Menu.products),
            selectinload(Display.playlist_override)
            .selectinload(Playlist.items)
            .selectinload(PlaylistItem.menu)
            .selectinload(Menu.products),
        )
        .filter_by(browser_code=normalized, archived=False)
        .first()
    )


def load_display_for_activation_code(activation_code: str) -> Display | None:
    normalized = normalize_activation_code(activation_code)
    if not normalized:
        return None
    return (
        Display.query.options(selectinload(Display.location))
        .filter_by(activation_code=normalized, archived=False)
        .first()
    )


def refresh_display_activation_code(
    display: Display, *, lifetime_minutes: int = 30
) -> Display:
    display.activation_code = generate_display_activation_code()
    display.activation_code_expires_at = datetime.utcnow() + timedelta(
        minutes=max(int(lifetime_minutes), 1)
    )
    db.session.commit()
    return display


def consume_display_activation_code(display: Display) -> Display:
    display.last_activated_at = datetime.utcnow()
    display.activation_code = None
    display.activation_code_expires_at = None
    db.session.commit()
    return display


def update_display_heartbeat(
    display: Display, *, remote_addr: str | None, user_agent: str | None
) -> None:
    display.last_seen_at = datetime.utcnow()
    display.last_seen_ip = (remote_addr or "")[:64] or None
    display.last_seen_user_agent = (user_agent or "")[:255] or None
    db.session.commit()


def build_display_manifest(display: Display) -> dict[str, Any]:
    slides = resolve_display_slides(display)
    playlist = display.effective_playlist
    return {
        "display": {
            "id": display.id,
            "name": display.name,
            "location_name": display.location.name if display.location else "",
            "is_online": display.is_online,
            "public_token": display.public_token,
            "browser_code": display.browser_code,
        },
        "playlist": {
            "id": playlist.id if playlist is not None else None,
            "name": playlist.name if playlist is not None else "",
            "uses_location_fallback": playlist is None,
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "slides": slides,
    }


def resolve_display_slides(display: Display) -> list[dict[str, Any]]:
    playlist = display.effective_playlist
    slides: list[dict[str, Any]] = []

    if playlist is not None and not playlist.archived:
        ordered_items = sorted(playlist.items, key=lambda item: (item.position, item.id))
        for item in ordered_items:
            slides.append(_playlist_item_to_slide(display, item))

    if slides:
        return slides

    fallback_menu = display.location.current_menu if display.location else None
    return [
        _build_menu_slide(
            fallback_menu,
            duration_seconds=20,
            source_type=PlaylistItem.SOURCE_LOCATION_MENU,
            fallback=True,
        )
    ]


def _playlist_item_to_slide(
    display: Display, item: PlaylistItem
) -> dict[str, Any]:
    if item.source_type == PlaylistItem.SOURCE_LOCATION_MENU:
        menu = display.location.current_menu if display.location else None
    else:
        menu = item.menu
    return _build_menu_slide(
        menu,
        duration_seconds=item.duration_seconds,
        source_type=item.source_type,
        fallback=False,
    )


def _build_menu_slide(
    menu: Menu | None,
    *,
    duration_seconds: int,
    source_type: str,
    fallback: bool,
) -> dict[str, Any]:
    products: list[dict[str, Any]] = []
    if menu is not None:
        ordered_products = sorted(
            menu.products, key=lambda product: ((product.name or "").lower(), product.id)
        )
        products = [_product_to_payload(product) for product in ordered_products]

    return {
        "type": "menu",
        "source_type": source_type,
        "fallback": fallback,
        "duration_seconds": max(int(duration_seconds or 0), 5),
        "menu": {
            "id": menu.id if menu is not None else None,
            "name": menu.name if menu is not None else "No menu assigned",
            "description": menu.description if menu is not None else "",
        },
        "products": products,
        "empty": menu is None or not products,
    }


def _product_to_payload(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "name": product.name,
        "price": round(float(product.price or 0.0), 2),
    }
