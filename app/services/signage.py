from __future__ import annotations

import secrets
import string
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    BoardTemplate,
    BoardTemplateBlock,
    Display,
    Location,
    Menu,
    Playlist,
    PlaylistItem,
    Product,
)
from app.services.signage_media import signage_media_public_url

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


def _display_query_options():
    return (
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
        selectinload(Display.board_template)
        .selectinload(BoardTemplate.blocks)
        .selectinload(BoardTemplateBlock.media_asset),
    )


def load_display_for_player(public_token: str) -> Display | None:
    return (
        Display.query.options(*_display_query_options())
        .filter_by(public_token=public_token, archived=False)
        .first()
    )


def load_display_for_browser_code(browser_code: str) -> Display | None:
    normalized = normalize_display_browser_code(browser_code)
    if not normalized:
        return None
    return (
        Display.query.options(*_display_query_options())
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
    layout = resolve_display_layout(display)
    slides = resolve_display_slides(display, layout)
    playlist = display.effective_playlist
    return {
        "display": {
            "id": display.id,
            "name": display.name,
            "location_name": display.location.name if display.location else "",
            "is_online": display.is_online,
            "public_token": display.public_token,
            "browser_code": display.browser_code,
            "board_template_id": display.board_template_id,
        },
        "layout": layout,
        "playlist": {
            "id": playlist.id if playlist is not None else None,
            "name": playlist.name if playlist is not None else "",
            "uses_location_fallback": playlist is None,
        },
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "slides": slides,
    }


def resolve_display_layout(display: Display) -> dict[str, Any]:
    template = display.effective_board_template

    def _positive_int(value: Any, fallback: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return fallback
        return number if number > 0 else fallback

    layout = {
        "source": "board_template" if template is not None else "display",
        "template": {
            "id": template.id if template is not None else None,
            "name": template.name if template is not None else "",
            "theme": template.theme if template is not None else BoardTemplate.THEME_AURORA,
        },
        "grid_columns": BoardTemplate.GRID_COLUMNS,
        "grid_rows": BoardTemplate.GRID_ROWS,
        "canvas_width": _positive_int(
            template.canvas_width if template is not None else 1920, 1920
        ),
        "canvas_height": _positive_int(
            template.canvas_height if template is not None else 1080, 1080
        ),
        "board_columns": _positive_int(
            template.menu_columns if template is not None else display.board_columns,
            3,
        ),
        "board_rows": _positive_int(
            template.menu_rows if template is not None else display.board_rows,
            4,
        ),
        "show_prices": bool(
            template.show_prices if template is not None else display.show_prices
        ),
        "show_menu_description": bool(
            template.show_menu_description
            if template is not None
            else display.show_menu_description
        ),
        "show_page_indicator": bool(
            template.show_page_indicator if template is not None else True
        ),
        "selected_product_ids": display.selected_product_id_list,
        "brand_label": (
            (template.brand_label or "").strip()
            if template is not None
            else "Digital Menu Board"
        )
        or "Digital Menu Board",
        "brand_name": (
            (template.brand_name or "").strip()
            if template is not None
            else display.name
        )
        or display.name,
        "side_panel_position": (
            template.side_panel_position if template is not None else BoardTemplate.PANEL_NONE
        ),
        "side_panel_width_percent": _positive_int(
            template.side_panel_width_percent if template is not None else 30, 30
        ),
        "side_title": (template.side_title or "").strip() if template is not None else "",
        "side_body": (template.side_body or "").strip() if template is not None else "",
        "side_image_url": (template.side_image_url or "").strip() if template is not None else "",
        "footer_text": (template.footer_text or "").strip() if template is not None else "",
        "uses_blocks": False,
        "blocks": [],
    }

    if template is not None and template.blocks:
        blocks = []
        for block in sorted(
            template.blocks,
            key=lambda entry: (entry.position, entry.id or 0),
        ):
            blocks.append(
                {
                    "id": block.id,
                    "position": block.position,
                    "type": block.block_type,
                    "width_units": _positive_int(block.width_units, 6),
                    "title": (block.title or "").strip(),
                    "body": (block.body or "").strip(),
                    "media_asset_id": block.media_asset_id,
                    "media_url": _resolve_block_media_url(block),
                    "grid_x": _positive_int(block.grid_x, 1),
                    "grid_y": _positive_int(block.grid_y, 1),
                    "grid_width": _positive_int(block.grid_width, 12),
                    "grid_height": _positive_int(block.grid_height, 10),
                    "menu_columns": _positive_int(block.menu_columns, 2),
                    "menu_rows": _positive_int(block.menu_rows, 4),
                    "show_title": bool(block.show_title),
                    "show_prices": bool(block.show_prices),
                    "show_menu_description": bool(block.show_menu_description),
                    "selected_product_ids": block.selected_product_id_list,
                }
            )
        layout["uses_blocks"] = bool(blocks)
        layout["blocks"] = blocks

    return layout


def resolve_display_slides(display: Display, layout: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    layout = layout or resolve_display_layout(display)
    playlist = display.effective_playlist
    slides: list[dict[str, Any]] = []

    if playlist is not None and not playlist.archived:
        ordered_items = sorted(playlist.items, key=lambda item: (item.position, item.id))
        for item in ordered_items:
            slides.extend(_playlist_item_to_slides(display, layout, item))

    if slides:
        return slides

    fallback_menu = display.location.current_menu if display.location else None
    if layout.get("uses_blocks"):
        return _build_template_block_slides(
            display,
            layout,
            fallback_menu,
            duration_seconds=20,
            source_type=PlaylistItem.SOURCE_LOCATION_MENU,
            fallback=True,
        )
    return _build_menu_slides(
        display,
        layout,
        fallback_menu,
        duration_seconds=20,
        source_type=PlaylistItem.SOURCE_LOCATION_MENU,
        fallback=True,
    )


def _playlist_item_to_slides(
    display: Display, layout: dict[str, Any], item: PlaylistItem
) -> list[dict[str, Any]]:
    if item.source_type == PlaylistItem.SOURCE_LOCATION_MENU:
        menu = display.location.current_menu if display.location else None
    else:
        menu = item.menu
    if layout.get("uses_blocks"):
        return _build_template_block_slides(
            display,
            layout,
            menu,
            duration_seconds=item.duration_seconds,
            source_type=item.source_type,
            fallback=False,
        )
    return _build_menu_slides(
        display,
        layout,
        menu,
        duration_seconds=item.duration_seconds,
        source_type=item.source_type,
        fallback=False,
    )


def _resolve_products_for_selection(
    menu: Menu | None,
    selected_product_ids: list[int] | None = None,
) -> list[Product]:
    if menu is None:
        return []
    ordered_products = sorted(
        menu.products, key=lambda product: ((product.name or "").lower(), product.id)
    )
    if not selected_product_ids:
        return ordered_products

    product_by_id = {product.id: product for product in ordered_products}
    filtered_products: list[Product] = []
    for product_id in selected_product_ids:
        product = product_by_id.get(product_id)
        if product is not None:
            filtered_products.append(product)
    return filtered_products


def _resolve_display_products(display: Display, menu: Menu | None) -> list[Product]:
    return _resolve_products_for_selection(menu, display.selected_product_id_list)


def _resolve_block_media_url(block: BoardTemplateBlock) -> str:
    if block.media_asset is not None:
        return signage_media_public_url(block.media_asset)
    return (block.media_url or "").strip()


def _paginate_products(
    products: list[Product], products_per_page: int
) -> list[list[Product]]:
    page_size = max(int(products_per_page or 0), 1)
    if not products:
        return [[]]
    return [
        products[index : index + page_size]
        for index in range(0, len(products), page_size)
    ]


def _menu_to_payload(menu: Menu | None) -> dict[str, Any]:
    return {
        "id": menu.id if menu is not None else None,
        "name": menu.name if menu is not None else "No menu assigned",
        "description": menu.description if menu is not None else "",
    }


def _build_menu_slides(
    display: Display,
    layout: dict[str, Any],
    menu: Menu | None,
    *,
    duration_seconds: int,
    source_type: str,
    fallback: bool,
) -> list[dict[str, Any]]:
    resolved_products = _resolve_display_products(display, menu)
    products_per_page = max(
        int(layout.get("board_columns") or 0) * int(layout.get("board_rows") or 0),
        1,
    )
    pages = _paginate_products(resolved_products, products_per_page)
    menu_payload = _menu_to_payload(menu)
    show_summary_description = bool(layout.get("show_menu_description"))

    slides: list[dict[str, Any]] = []
    for page_index, product_page in enumerate(pages, start=1):
        slides.append(
            {
                "type": "menu",
                "source_type": source_type,
                "fallback": fallback,
                "duration_seconds": max(int(duration_seconds or 0), 5),
                "page_index": page_index,
                "page_count": len(pages),
                "menu": menu_payload,
                "summary_kicker": (
                    "Location Menu"
                    if source_type == PlaylistItem.SOURCE_LOCATION_MENU
                    else "Specific Menu"
                ),
                "summary_title": menu_payload["name"],
                "summary_description": (
                    menu_payload["description"] if show_summary_description else ""
                ),
                "show_summary_description": show_summary_description,
                "products": [_product_to_payload(product) for product in product_page],
                "empty": menu is None or not product_page,
            }
        )
    return slides


def _build_template_block_slides(
    display: Display,
    layout: dict[str, Any],
    menu: Menu | None,
    *,
    duration_seconds: int,
    source_type: str,
    fallback: bool,
) -> list[dict[str, Any]]:
    template = display.effective_board_template
    if template is None or not template.blocks:
        return _build_menu_slides(
            display,
            layout,
            menu,
            duration_seconds=duration_seconds,
            source_type=source_type,
            fallback=fallback,
        )

    ordered_blocks = sorted(
        template.blocks,
        key=lambda entry: (entry.position, entry.id or 0),
    )
    block_definitions: list[dict[str, Any]] = []
    overall_page_count = 1
    primary_menu_block: BoardTemplateBlock | None = None

    for block in ordered_blocks:
        block_payload = {
            "id": block.id,
            "position": block.position,
            "type": block.block_type,
            "width_units": max(int(block.width_units or 0), 1),
            "title": (block.title or "").strip(),
            "body": (block.body or "").strip(),
            "media_asset_id": block.media_asset_id,
            "media_url": _resolve_block_media_url(block),
            "grid_x": max(int(block.grid_x or 0), 1),
            "grid_y": max(int(block.grid_y or 0), 1),
            "grid_width": max(int(block.grid_width or 0), 1),
            "grid_height": max(int(block.grid_height or 0), 1),
            "show_title": bool(block.show_title),
            "show_prices": bool(block.show_prices),
            "show_menu_description": bool(block.show_menu_description),
        }
        if block.block_type == BoardTemplateBlock.TYPE_MENU:
            selected_product_ids = (
                block.selected_product_id_list or display.selected_product_id_list
            )
            resolved_products = _resolve_products_for_selection(
                menu, selected_product_ids
            )
            page_size = max(
                int(block.menu_columns or 0) * int(block.menu_rows or 0),
                1,
            )
            block_pages = [
                [_product_to_payload(product) for product in page]
                for page in _paginate_products(resolved_products, page_size)
            ]
            block_payload.update(
                {
                    "menu": _menu_to_payload(menu),
                    "menu_columns": max(int(block.menu_columns or 0), 1),
                    "menu_rows": max(int(block.menu_rows or 0), 1),
                    "page_count": len(block_pages),
                    "selected_product_ids": selected_product_ids,
                    "_pages": block_pages,
                }
            )
            overall_page_count = max(overall_page_count, len(block_pages))
            if primary_menu_block is None:
                primary_menu_block = block
        block_definitions.append(block_payload)

    if primary_menu_block is not None and menu is not None:
        summary_kicker = (
            "Location Menu"
            if source_type == PlaylistItem.SOURCE_LOCATION_MENU
            else "Specific Menu"
        )
        summary_title = menu.name or display.name
        show_summary_description = bool(
            primary_menu_block.show_menu_description or layout.get("show_menu_description")
        )
        summary_description = (
            menu.description if show_summary_description else ""
        )
        menu_payload = _menu_to_payload(menu)
    else:
        summary_kicker = "Board Template"
        summary_title = (template.name or "").strip() or display.name
        show_summary_description = False
        summary_description = ""
        menu_payload = None

    slides: list[dict[str, Any]] = []
    for page_offset in range(overall_page_count):
        slide_blocks: list[dict[str, Any]] = []
        for block_payload in block_definitions:
            slide_block = {
                key: value
                for key, value in block_payload.items()
                if key != "_pages"
            }
            if block_payload["type"] == BoardTemplateBlock.TYPE_MENU:
                pages = block_payload.get("_pages") or [[]]
                page_count = len(pages)
                block_page_index = (page_offset % page_count) + 1
                products = pages[block_page_index - 1]
                slide_block.update(
                    {
                        "page_index": block_page_index,
                        "page_count": page_count,
                        "products": products,
                        "empty": menu is None or not products,
                    }
                )
            slide_blocks.append(slide_block)

        slides.append(
            {
                "type": "board",
                "source_type": source_type,
                "fallback": fallback,
                "duration_seconds": max(int(duration_seconds or 0), 5),
                "page_index": page_offset + 1,
                "page_count": overall_page_count,
                "menu": menu_payload,
                "summary_kicker": summary_kicker,
                "summary_title": summary_title,
                "summary_description": summary_description,
                "show_summary_description": show_summary_description,
                "blocks": slide_blocks,
                "empty": not slide_blocks,
            }
        )
    return slides


def _product_to_payload(product: Product) -> dict[str, Any]:
    return {
        "id": product.id,
        "name": product.name,
        "price": round(float(product.price or 0.0), 2),
    }
