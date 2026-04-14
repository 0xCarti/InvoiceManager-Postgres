from werkzeug.security import generate_password_hash

from app import db
from app.models import Display, Location, Menu, Playlist, PlaylistItem, Product, User
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
        display = Display(name="Front TV", location=location, public_token="front-token")
        db.session.add_all([playlist, location, display])
        db.session.commit()

    response = client.get("/api/player/front-token/manifest")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["display"]["name"] == "Front TV"
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
        )
        db.session.add_all([default_playlist, override_playlist, location, display])
        db.session.commit()

    response = client.get("/api/player/bar-token/manifest")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["playlist"]["name"] == "Override Rotation"
    assert [slide["menu"]["name"] for slide in payload["slides"]] == ["Dinner"]


def test_player_heartbeat_updates_display_status(client, app):
    with app.app_context():
        location = Location(name="Kitchen")
        display = Display(name="Kitchen TV", location=location, public_token="heartbeat-token")
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
