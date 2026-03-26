import os
import shutil
import sqlite3
import io
import json

from app.models import ActivityLog, User
from app.utils.backup import RestoreCompatibilityResult, RestoreSummary
from tests.utils import login
from app.utils.activity import flush_activity_logs
from app.utils.backup import create_backup
from sqlalchemy.exc import OperationalError


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


def test_restore_backup_file_permissive_mode_shows_partial_restore_message(
    client, app, monkeypatch
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    _create_sqlite_backup(app, "permissive_partial.db")

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        lambda *_args, **_kwargs: RestoreCompatibilityResult(compatible=True, issues=[]),
    )
    monkeypatch.setattr(
        "app.routes.auth_routes.restore_backup",
        lambda *_args, **_kwargs: RestoreSummary(
            mode="permissive",
            inserted_count=12,
            skipped_count=2,
            affected_tables=["user", "setting"],
            quarantine_report="restore_quarantine_20260326_123456.json",
        ),
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/permissive_partial.db?restore_mode=permissive",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Backup restored from permissive_partial.db" in response.data
    assert b"Partial restore completed in permissive mode" in response.data
    assert b"restore_quarantine_20260326_123456.json" in response.data


def test_restore_backup_file_preflight_exception_creates_diagnostic_report(
    client, app, monkeypatch, caplog
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    _create_sqlite_backup(app, "preflight_exception.db")

    def _raise_preflight(*_args, **_kwargs):
        raise OperationalError(
            "SELECT total FROM invoice",
            {},
            sqlite3.OperationalError("no such column: total"),
        )

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        _raise_preflight,
    )
    monkeypatch.setattr(
        "app.routes.auth_routes.restore_backup",
        lambda *_args, **_kwargs: RestoreSummary(
            mode="strict", inserted_count=1, skipped_count=0, affected_tables=["user"]
        ),
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/preflight_exception.db",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Restore diagnostic ID:" in response.data
    assert b"restore_preflight_diag_" in response.data
    assert "stage=preflight" in caplog.text

    report_files = sorted(
        name
        for name in os.listdir(app.config["BACKUP_FOLDER"])
        if name.startswith("restore_preflight_diag_") and name.endswith(".json")
    )
    assert report_files
    report_path = os.path.join(app.config["BACKUP_FOLDER"], report_files[-1])
    with open(report_path, encoding="utf-8") as handle:
        payload = json.load(handle)
    assert payload["stage"] == "preflight"
    assert payload["filename"] == "preflight_exception.db"
    assert payload["exception_class"] == "OperationalError"
    assert payload["context"]["column"] == "total"

    with app.app_context():
        flush_activity_logs()
        activities = [row.activity for row in ActivityLog.query.order_by(ActivityLog.id).all()]
        assert any("Restore preflight diagnostic" in item for item in activities)


def test_restore_backup_upload_preflight_exception_creates_diagnostic_report(
    client, app, monkeypatch
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    def _raise_preflight(*_args, **_kwargs):
        raise OperationalError(
            "INSERT INTO user(email) VALUES (?)",
            {"email": "admin@example.com"},
            sqlite3.OperationalError("table user has no column named email"),
        )

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        _raise_preflight,
    )
    monkeypatch.setattr(
        "app.routes.auth_routes.restore_backup",
        lambda *_args, **_kwargs: RestoreSummary(
            mode="strict", inserted_count=1, skipped_count=0, affected_tables=["user"]
        ),
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore",
            data={
                "file": (io.BytesIO(b"SQLite format 3\x00fake"), "upload_preflight.db"),
                "restore_mode": "strict",
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Restore diagnostic ID:" in response.data
    assert b"upload_preflight.db" in response.data
    report_files = sorted(
        name
        for name in os.listdir(app.config["BACKUP_FOLDER"])
        if name.startswith("restore_preflight_diag_") and name.endswith(".json")
    )
    assert report_files
