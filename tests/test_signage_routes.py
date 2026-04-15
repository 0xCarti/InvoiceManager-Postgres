import io
from datetime import datetime, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

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
    SignageMediaAsset,
    User,
)
from tests.permission_helpers import grant_signage_permissions
from tests.utils import login


def _create_menu(name: str, product_names: list[str]) -> Menu:
    menu = Menu(name=name, description=f"{name} description")
    for index, product_name in enumerate(product_names, start=1):
        menu.products.append(
            Product(name=product_name, price=float(index) + 4.0, cost=1.0)
        )
    db.session.add(menu)
    db.session.flush()
    return menu


def _create_signage_media_asset(app, *, filename: str, content: bytes, media_type: str) -> SignageMediaAsset:
    upload_dir = Path(app.config["UPLOAD_FOLDER"]) / "signage_media"
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_path = upload_dir / filename
    storage_path.write_bytes(content)
    asset = SignageMediaAsset(
        name=filename,
        original_filename=filename,
        media_type=media_type,
        content_type="image/png" if media_type == SignageMediaAsset.TYPE_IMAGE else "video/mp4",
        file_size_bytes=len(content),
        sha256="test-" + filename,
        storage_path=str(storage_path),
    )
    db.session.add(asset)
    db.session.flush()
    return asset


def test_display_manifest_inherits_location_playlist_and_menu(client, app):
    with app.app_context():
        breakfast = _create_menu("Breakfast", ["Coffee", "Bagel"])
        lunch = _create_menu("Lunch", ["Burger"])
        playlist = Playlist(name="Counter Rotation")
        playlist.items = [
            PlaylistItem(
                position=0,
                source_type=PlaylistItem.SOURCE_LOCATION_MENU,
                duration_seconds=12,
            ),
            PlaylistItem(
                position=1,
                source_type=PlaylistItem.SOURCE_MENU,
                menu=lunch,
                duration_seconds=18,
            ),
        ]
        location = Location(
            name="Front Counter",
            current_menu=breakfast,
            default_playlist=playlist,
        )
        display = Display(
            name="Front TV",
            location=location,
            public_token="front-token",
            browser_code="FRNT23",
        )
        db.session.add_all([playlist, location, display])
        db.session.commit()

    response = client.get("/api/player/front-token/manifest")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["display"]["name"] == "Front TV"
    assert payload["display"]["browser_code"] == "FRNT23"
    assert payload["playlist"]["name"] == "Counter Rotation"
    assert [slide["menu"]["name"] for slide in payload["slides"]] == [
        "Breakfast",
        "Lunch",
    ]
    assert payload["slides"][0]["source_type"] == PlaylistItem.SOURCE_LOCATION_MENU
    assert payload["slides"][1]["products"][0]["name"] == "Burger"


def test_display_manifest_prefers_display_override_playlist(client, app):
    with app.app_context():
        breakfast = _create_menu("Override Breakfast", ["Coffee"])
        dinner = _create_menu("Dinner", ["Pasta"])
        default_playlist = Playlist(name="Inherited Rotation")
        default_playlist.items = [
            PlaylistItem(
                position=0,
                source_type=PlaylistItem.SOURCE_LOCATION_MENU,
                duration_seconds=10,
            )
        ]
        override_playlist = Playlist(name="Override Rotation")
        override_playlist.items = [
            PlaylistItem(
                position=0,
                source_type=PlaylistItem.SOURCE_MENU,
                menu=dinner,
                duration_seconds=25,
            )
        ]
        location = Location(
            name="Bar",
            current_menu=breakfast,
            default_playlist=default_playlist,
        )
        display = Display(
            name="Bar TV",
            location=location,
            playlist_override=override_playlist,
            public_token="bar-token",
            browser_code="BARTV2",
        )
        db.session.add_all([default_playlist, override_playlist, location, display])
        db.session.commit()

    response = client.get("/api/player/bar-token/manifest")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["playlist"]["name"] == "Override Rotation"
    assert [slide["menu"]["name"] for slide in payload["slides"]] == ["Dinner"]


def test_display_manifest_filters_products_and_paginates(client, app):
    with app.app_context():
        menu = _create_menu(
            "Concourse Board",
            ["Burger", "Fries", "Hot Dog", "Nachos", "Popcorn", "Pretzel"],
        )
        location = Location(name="Concourse", current_menu=menu)
        selected_ids = [menu.products[0].id, menu.products[2].id, menu.products[4].id]
        display = Display(
            name="Concourse TV",
            location=location,
            public_token="concourse-token",
            browser_code="CNCRSE",
            board_columns=1,
            board_rows=2,
            selected_product_ids=",".join(str(product_id) for product_id in selected_ids),
        )
        db.session.add_all([location, display])
        db.session.commit()

    response = client.get("/api/player/concourse-token/manifest")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["layout"]["board_columns"] == 1
    assert payload["layout"]["board_rows"] == 2
    assert payload["layout"]["selected_product_ids"] == selected_ids
    assert len(payload["slides"]) == 2
    assert [product["name"] for product in payload["slides"][0]["products"]] == [
        "Burger",
        "Hot Dog",
    ]
    assert [product["name"] for product in payload["slides"][1]["products"]] == [
        "Popcorn",
    ]
    assert payload["slides"][0]["page_index"] == 1
    assert payload["slides"][0]["page_count"] == 2


def test_board_template_overrides_display_layout_and_renders_side_panel(client, app):
    with app.app_context():
        menu = _create_menu(
            "Arena Combos",
            ["Combo A", "Combo B", "Combo C", "Combo D", "Combo E"],
        )
        template = BoardTemplate(
            name="Arena 1080p",
            theme=BoardTemplate.THEME_CONCOURSE,
            canvas_width=1920,
            canvas_height=1080,
            menu_columns=2,
            menu_rows=2,
            show_prices=False,
            show_menu_description=True,
            show_page_indicator=True,
            brand_label="Arena Specials",
            brand_name="Arena Combos",
            side_panel_position=BoardTemplate.PANEL_RIGHT,
            side_panel_width_percent=30,
            side_title="Hat Trick Combo",
            side_body="Big flavor. Big value.",
            footer_text="Section A pickup",
        )
        location = Location(name="Upper Bowl", current_menu=menu)
        display = Display(
            name="Upper Bowl TV",
            location=location,
            board_template=template,
            public_token="arena-token",
            browser_code="ARNA23",
            board_columns=4,
            board_rows=1,
            show_prices=True,
        )
        db.session.add_all([template, location, display])
        db.session.commit()

    manifest_response = client.get("/api/player/arena-token/manifest")

    assert manifest_response.status_code == 200
    payload = manifest_response.get_json()
    assert payload["layout"]["source"] == "board_template"
    assert payload["layout"]["template"]["name"] == "Arena 1080p"
    assert payload["layout"]["board_columns"] == 2
    assert payload["layout"]["board_rows"] == 2
    assert payload["layout"]["show_prices"] is False
    assert payload["layout"]["side_panel_position"] == BoardTemplate.PANEL_RIGHT
    assert payload["layout"]["side_title"] == "Hat Trick Combo"
    assert len(payload["slides"]) == 2

    player_response = client.get("/s/ARNA23")

    assert player_response.status_code == 200
    assert b"Arena Combos" in player_response.data
    assert b"Hat Trick Combo" in player_response.data
    assert b"Section A pickup" in player_response.data
    assert b"/api/player/arena-token/manifest" in player_response.data


def test_board_template_blocks_render_menu_text_image_and_video(client, app):
    with app.app_context():
        menu = _create_menu(
            "Board Blocks",
            ["Burger", "Fries", "Nachos", "Pretzel"],
        )
        image_asset = _create_signage_media_asset(
            app,
            filename="promo.png",
            content=b"png-data",
            media_type=SignageMediaAsset.TYPE_IMAGE,
        )
        image_asset_id = image_asset.id
        template = BoardTemplate(
            name="Block Layout",
            theme=BoardTemplate.THEME_AURORA,
            canvas_width=1920,
            canvas_height=1080,
            footer_text="Intermission pickup",
        )
        template.blocks = [
            BoardTemplateBlock(
                position=0,
                block_type=BoardTemplateBlock.TYPE_MENU,
                width_units=6,
                grid_x=1,
                grid_y=1,
                grid_width=12,
                grid_height=12,
                title="Featured Snacks",
                menu_columns=1,
                menu_rows=2,
                show_title=True,
                show_prices=True,
                show_menu_description=True,
            ),
            BoardTemplateBlock(
                position=1,
                block_type=BoardTemplateBlock.TYPE_TEXT,
                width_units=2,
                grid_x=13,
                grid_y=1,
                grid_width=4,
                grid_height=5,
                title="Specials",
                body="Add a drink for $2.00",
                show_title=True,
            ),
            BoardTemplateBlock(
                position=2,
                block_type=BoardTemplateBlock.TYPE_IMAGE,
                width_units=2,
                grid_x=17,
                grid_y=1,
                grid_width=8,
                grid_height=5,
                title="Combo Poster",
                media_asset=image_asset,
                show_title=True,
            ),
            BoardTemplateBlock(
                position=3,
                block_type=BoardTemplateBlock.TYPE_VIDEO,
                width_units=2,
                grid_x=13,
                grid_y=6,
                grid_width=12,
                grid_height=7,
                title="Ad Loop",
                media_url="https://example.com/promo.mp4",
                show_title=True,
            ),
        ]
        location = Location(name="Main Concourse", current_menu=menu)
        display = Display(
            name="Main Board",
            location=location,
            board_template=template,
            public_token="block-token",
            browser_code="BLK234",
        )
        db.session.add_all([template, location, display])
        db.session.commit()

    manifest_response = client.get("/api/player/block-token/manifest")

    assert manifest_response.status_code == 200
    payload = manifest_response.get_json()
    assert payload["layout"]["uses_blocks"] is True
    assert [block["type"] for block in payload["layout"]["blocks"]] == [
        BoardTemplateBlock.TYPE_MENU,
        BoardTemplateBlock.TYPE_TEXT,
        BoardTemplateBlock.TYPE_IMAGE,
        BoardTemplateBlock.TYPE_VIDEO,
    ]
    assert payload["layout"]["blocks"][0]["grid_x"] == 1
    assert payload["layout"]["blocks"][0]["grid_width"] == 12
    assert len(payload["slides"]) == 2
    assert payload["slides"][0]["type"] == "board"
    assert payload["slides"][0]["summary_title"] == "Board Blocks"
    first_menu_block = payload["slides"][0]["blocks"][0]
    second_menu_block = payload["slides"][1]["blocks"][0]
    assert [product["name"] for product in first_menu_block["products"]] == [
        "Burger",
        "Fries",
    ]
    assert [product["name"] for product in second_menu_block["products"]] == [
        "Nachos",
        "Pretzel",
    ]
    assert payload["slides"][0]["blocks"][1]["body"] == "Add a drink for $2.00"
    assert payload["slides"][0]["blocks"][2]["media_url"].endswith(
        f"/signage/media/{image_asset_id}/file/promo.png"
    )
    assert payload["slides"][0]["blocks"][3]["media_url"] == "https://example.com/promo.mp4"

    player_response = client.get("/s/BLK234")

    assert player_response.status_code == 200
    assert b"Featured Snacks" in player_response.data
    assert b"Add a drink for $2.00" in player_response.data
    assert (
        f"/signage/media/{image_asset_id}/file/promo.png".encode("utf-8")
        in player_response.data
    )
    assert b"https://example.com/promo.mp4" in player_response.data


def test_player_heartbeat_updates_display_status(client, app):
    with app.app_context():
        location = Location(name="Kitchen")
        display = Display(
            name="Kitchen TV",
            location=location,
            public_token="heartbeat-token",
            browser_code="KITCH2",
        )
        db.session.add_all([location, display])
        db.session.commit()
        display_id = display.id

    response = client.post(
        "/api/player/heartbeat-token/heartbeat",
        headers={"User-Agent": "pytest-signage"},
    )

    assert response.status_code == 200
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display is not None
        assert display.last_seen_at is not None
        assert display.last_seen_user_agent == "pytest-signage"
        assert display.is_online


def test_signage_user_can_issue_activation_code(client, app):
    with app.app_context():
        user = User(
            email="activate@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name="Activation Counter")
        display = Display(name="Activation TV", location=location)
        db.session.add_all([user, location, display])
        db.session.commit()
        grant_signage_permissions(user)
        display_id = display.id

    with client:
        login(client, "activate@example.com", "pass")
        response = client.post(
            f"/signage/displays/{display_id}/activation-code",
            data={},
            follow_redirects=True,
        )

    assert response.status_code == 200
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display is not None
        assert display.activation_code
        assert display.activation_code_expires_at is not None
        assert display.activation_code_expires_at > datetime.utcnow()


def test_short_player_url_loads_display(client, app):
    with app.app_context():
        menu = _create_menu("Short URL Menu", ["Fries"])
        location = Location(name="Drive Thru", current_menu=menu)
        display = Display(
            name="Drive Thru TV",
            location=location,
            public_token="short-token",
            browser_code="DRV234",
        )
        db.session.add_all([location, display])
        db.session.commit()

    response = client.get("/s/drv234")

    assert response.status_code == 200
    assert b"Drive Thru TV" in response.data
    assert b"Short URL Menu" in response.data
    assert b"Fries" in response.data
    assert b"/api/player/short-token/manifest" in response.data


def test_tizen_activation_consumes_display_code(client, app):
    with app.app_context():
        location = Location(name="Hosted App Counter")
        display = Display(
            name="Hosted App TV",
            location=location,
            public_token="activate-token",
            browser_code="HOST23",
            activation_code="ABC123",
            activation_code_expires_at=datetime.utcnow() + timedelta(minutes=20),
        )
        db.session.add_all([location, display])
        db.session.commit()
        display_id = display.id

    response = client.post("/api/signage/tizen/activate", json={"code": "abc123"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["display"]["public_token"] == "activate-token"
    assert payload["display"]["browser_code"] == "HOST23"
    assert payload["player_url"].endswith("/s/HOST23")
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display is not None
        assert display.activation_code is None
        assert display.activation_code_expires_at is None
        assert display.last_activated_at is not None


def test_tizen_activation_rejects_expired_code(client, app):
    with app.app_context():
        location = Location(name="Expired Counter")
        display = Display(
            name="Expired TV",
            location=location,
            browser_code="EXP234",
            activation_code="ZZZ999",
            activation_code_expires_at=datetime.utcnow() - timedelta(minutes=1),
        )
        db.session.add_all([location, display])
        db.session.commit()

    response = client.post("/api/signage/tizen/activate", json={"code": "ZZZ999"})

    assert response.status_code == 410
    payload = response.get_json()
    assert payload["ok"] is False
    assert "expired" in payload["error"].lower()


def test_signage_user_can_create_playlist(client, app):
    with app.app_context():
        user = User(
            email="signage@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        _create_menu("Rotation One", ["Nachos"])
        second_menu = _create_menu("Rotation Two", ["Pretzel"])
        db.session.commit()
        grant_signage_permissions(user)
        second_menu_id = second_menu.id

    with client:
        login(client, "signage@example.com", "pass")
        response = client.post(
            "/signage/playlists/add",
            data={
                "name": "Lobby Rotation",
                "description": "Main lobby playlist",
                "items-0-source_type": PlaylistItem.SOURCE_LOCATION_MENU,
                "items-0-menu_id": "0",
                "items-0-duration_seconds": "12",
                "items-1-source_type": PlaylistItem.SOURCE_MENU,
                "items-1-menu_id": str(second_menu_id),
                "items-1-duration_seconds": "20",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    with app.app_context():
        playlist = Playlist.query.filter_by(name="Lobby Rotation").first()
        assert playlist is not None
        assert [item.source_type for item in playlist.items] == [
            PlaylistItem.SOURCE_LOCATION_MENU,
            PlaylistItem.SOURCE_MENU,
        ]
        assert playlist.items[1].menu_id == second_menu_id


def test_signage_user_can_create_board_template(client, app):
    with app.app_context():
        user = User(
            email="signage-template@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_signage_permissions(user)

    with client:
        login(client, "signage-template@example.com", "pass")
        response = client.post(
            "/signage/board-templates/add",
            data={
                "name": "Lobby Board",
                "description": "Main lobby layout",
                "canvas_width": "1920",
                "canvas_height": "1080",
                "theme": BoardTemplate.THEME_AURORA,
                "brand_label": "Digital Menu Board",
                "brand_name": "Prairie Grill",
                "menu_columns": "3",
                "menu_rows": "4",
                "side_panel_position": BoardTemplate.PANEL_RIGHT,
                "side_panel_width_percent": "30",
                "side_title": "Specials",
                "side_body": "Combo and promo text",
                "side_image_url": "https://example.com/promo.jpg",
                "footer_text": "Open during intermission",
                "show_prices": "y",
                "show_menu_description": "",
                "show_page_indicator": "y",
                "blocks-0-block_type": BoardTemplateBlock.TYPE_MENU,
                "blocks-0-width_units": "8",
                "blocks-0-grid_x": "1",
                "blocks-0-grid_y": "1",
                "blocks-0-grid_width": "16",
                "blocks-0-grid_height": "10",
                "blocks-0-title": "Entrees",
                "blocks-0-body": "",
                "blocks-0-media_asset_id": "0",
                "blocks-0-media_url": "",
                "blocks-0-menu_columns": "2",
                "blocks-0-menu_rows": "4",
                "blocks-0-show_title": "y",
                "blocks-0-show_prices": "y",
                "blocks-0-show_menu_description": "",
                "blocks-1-block_type": BoardTemplateBlock.TYPE_TEXT,
                "blocks-1-width_units": "4",
                "blocks-1-grid_x": "17",
                "blocks-1-grid_y": "1",
                "blocks-1-grid_width": "8",
                "blocks-1-grid_height": "10",
                "blocks-1-title": "Announcements",
                "blocks-1-body": "Two-for-one special after 7 PM",
                "blocks-1-media_asset_id": "0",
                "blocks-1-media_url": "",
                "blocks-1-menu_columns": "2",
                "blocks-1-menu_rows": "4",
                "blocks-1-show_title": "y",
                "blocks-1-show_prices": "",
                "blocks-1-show_menu_description": "",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    with app.app_context():
        template = BoardTemplate.query.filter_by(name="Lobby Board").first()
        assert template is not None
        assert template.side_panel_position == BoardTemplate.PANEL_RIGHT
        assert template.side_image_url == "https://example.com/promo.jpg"
        assert [block.block_type for block in template.blocks] == [
            BoardTemplateBlock.TYPE_MENU,
            BoardTemplateBlock.TYPE_TEXT,
        ]
        assert template.blocks[0].grid_x == 1
        assert template.blocks[0].grid_width == 16
        assert template.blocks[1].body == "Two-for-one special after 7 PM"


def test_signage_user_can_upload_media_asset(client, app):
    with app.app_context():
        user = User(
            email="signage-media@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_signage_permissions(user)

    with client:
        login(client, "signage-media@example.com", "pass")
        response = client.post(
            "/signage/media",
            data={
                "name": "Combo Poster",
                "file": (io.BytesIO(b"png-data"), "poster.png"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    assert response.status_code == 200
    with app.app_context():
        asset = SignageMediaAsset.query.filter_by(original_filename="poster.png").first()
        assert asset is not None
        assert asset.media_type == SignageMediaAsset.TYPE_IMAGE
        assert Path(asset.storage_path).exists()


def test_signage_media_delete_blocked_when_template_uses_asset(client, app):
    with app.app_context():
        asset = _create_signage_media_asset(
            app,
            filename="locked.png",
            content=b"png-data",
            media_type=SignageMediaAsset.TYPE_IMAGE,
        )
        template = BoardTemplate(name="Locked Asset Board")
        template.blocks = [
            BoardTemplateBlock(
                position=0,
                block_type=BoardTemplateBlock.TYPE_IMAGE,
                title="Poster",
                media_asset=asset,
                grid_x=1,
                grid_y=1,
                grid_width=8,
                grid_height=8,
            )
        ]
        user = User(
            email="signage-media-delete@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([template, user])
        db.session.commit()
        grant_signage_permissions(user)
        asset_id = asset.id

    with client:
        login(client, "signage-media-delete@example.com", "pass")
        response = client.post(
            f"/signage/media/{asset_id}/delete",
            data={},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"still used by a board template" in response.data
    with app.app_context():
        assert db.session.get(SignageMediaAsset, asset_id) is not None


def test_menu_delete_blocked_when_used_by_playlist(client, app):
    with app.app_context():
        menu = _create_menu("Protected Menu", ["Hot Dog"])
        playlist = Playlist(name="Protected Playlist")
        playlist.items = [
            PlaylistItem(
                position=0,
                source_type=PlaylistItem.SOURCE_MENU,
                menu=menu,
                duration_seconds=10,
            )
        ]
        db.session.add(playlist)
        db.session.commit()
        menu_id = menu.id

    with client:
        login(client, "admin@example.com", "adminpass")
        response = client.post(
            f"/menus/{menu_id}/delete",
            data={},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"used by a signage playlist" in response.data
    with app.app_context():
        assert db.session.get(Menu, menu_id) is not None
