import os
import shutil
import sqlite3

from app.models import ActivityLog, User
from app.utils.backup import RestoreCompatibilityResult
from tests.utils import login
from app.utils.activity import flush_activity_logs
from app.utils.backup import create_backup


def _create_sqlite_backup(app, filename):
    with app.app_context():
        generated = create_backup()
        source = os.path.join(app.config["BACKUP_FOLDER"], generated)
        destination = os.path.join(app.config["BACKUP_FOLDER"], filename)
        shutil.copyfile(source, destination)
    return destination


def test_restore_backup_file_compatible_metadata_flashes_success(
    client, app, monkeypatch
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    _create_sqlite_backup(app, "compatible.db")

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        lambda *_args, **_kwargs: RestoreCompatibilityResult(compatible=True, issues=[]),
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/compatible.db",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Backup restored from compatible.db" in response.data
    assert b"Incompatible backup" not in response.data


def test_restore_backup_file_incompatible_metadata_shows_failure_flash(
    client, app, monkeypatch
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    _create_sqlite_backup(app, "incompatible.db")

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        lambda *_args, **_kwargs: RestoreCompatibilityResult(compatible=False, issues=["missing marker"]),
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/incompatible.db",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Incompatible backup" in response.data
    assert b"Backup restored from incompatible.db" not in response.data


def test_restore_backup_file_prunes_invalid_favorites(client, app, monkeypatch):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    backup_path = _create_sqlite_backup(app, "invalid_favorites.db")
    with sqlite3.connect(backup_path) as conn:
        conn.execute(
            "UPDATE user SET favorites = ? WHERE email = ?",
            (
                "admin.backups,missing.endpoint,transfer.view_transfers,legacy.module",
                admin_email,
            ),
        )
        conn.commit()

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        lambda *_args, **_kwargs: RestoreCompatibilityResult(compatible=True, issues=[]),
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/invalid_favorites.db",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Favorites mode: pruned invalid favorites." in response.data

    with app.app_context():
        flush_activity_logs()
        admin_user = User.query.filter_by(email=admin_email).first()
        assert admin_user is not None
        assert admin_user.favorites == "admin.backups,transfer.view_transfers"
        activities = [row.activity for row in ActivityLog.query.order_by(ActivityLog.id).all()]
        assert any("Removed stale favorites" in item for item in activities)
        assert any("favorites_mode=cleaned" in item for item in activities)


def test_restore_backup_file_ignore_favorites_clears_all(client, app, monkeypatch):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    backup_path = _create_sqlite_backup(app, "ignore_favorites.db")
    with sqlite3.connect(backup_path) as conn:
        conn.execute(
            "UPDATE user SET favorites = ?",
            ("admin.backups,missing.endpoint,transfer.view_transfers",),
        )
        conn.commit()

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        lambda *_args, **_kwargs: RestoreCompatibilityResult(compatible=True, issues=[]),
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/ignore_favorites.db?ignore_favorites=1",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"Favorites mode: ignored backup favorites and cleared all user favorites."
        in response.data
    )

    with app.app_context():
        flush_activity_logs()
        users = User.query.all()
        assert users
        assert all((user.favorites or "") == "" for user in users)
        activities = [row.activity for row in ActivityLog.query.order_by(ActivityLog.id).all()]
        assert any("Cleared favorites for" in item for item in activities)
        assert any("favorites_mode=ignored" in item for item in activities)


def test_admin_backups_renders_with_invalid_favorites(client, app, monkeypatch):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    monkeypatch.setattr(
        "app.models.User.get_favorites",
        lambda self: ["missing.endpoint", "admin.backups"],
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/controlpanel/backups")

    assert response.status_code == 200
    assert b"Database Backups" in response.data
    assert b"BuildError" not in response.data


def test_admin_backups_renders_when_menu_endpoint_missing(client, app):
    removed = app.view_functions.pop("menu.view_menus", None)
    try:
        with client:
            login(
                client,
                os.getenv("ADMIN_EMAIL", "admin@example.com"),
                os.getenv("ADMIN_PASS", "adminpass"),
            )
            response = client.get("/controlpanel/backups")
    finally:
        if removed is not None:
            app.view_functions["menu.view_menus"] = removed

    assert response.status_code == 200
    assert b"Database Backups" in response.data
    assert b"BuildError" not in response.data
