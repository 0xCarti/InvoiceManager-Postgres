import os
import shutil
import sqlite3
import io
import json

from app import db
from app.models import ActivityLog, User
from app.utils.backup import RestoreCompatibilityResult, RestoreSummary
from tests.permission_helpers import grant_permissions
from tests.utils import login
from app.utils.activity import flush_activity_logs
from app.utils.backup import create_backup
from sqlalchemy.exc import OperationalError
from werkzeug.security import generate_password_hash


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
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/controlpanel/backups")
    follow_up = client.get("/controlpanel/backups", follow_redirects=True)

    assert follow_up.status_code == 200
    assert b"Backup restored from compatible.db" in follow_up.data
    assert b"Incompatible backup" not in follow_up.data


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
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/controlpanel/backups")
    follow_up = client.get("/controlpanel/backups", follow_redirects=True)

    assert follow_up.status_code == 200
    assert b"Incompatible backup" in follow_up.data
    assert b"Backup restored from incompatible.db" not in follow_up.data


def test_restore_backup_file_strict_mode_blocks_on_preflight_data_quality_warnings(
    client, app, monkeypatch
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    _create_sqlite_backup(app, "strict_blocked.db")
    restore_called = {"value": False}

    monkeypatch.setattr(
        "app.routes.auth_routes.validate_backup_file_compatibility",
        lambda *_args, **_kwargs: RestoreCompatibilityResult(
            compatible=True,
            issues=[],
            warnings=[
                "Foreign key orphan rows found for purchase_invoice_draft.purchase_invoice_draft_purchase_order_id_fkey -> purchase_order (1 row(s)). Sample key values: {'purchase_order_id': 1}.",
            ],
        ),
    )

    def _restore_should_not_run(*_args, **_kwargs):
        restore_called["value"] = True
        raise AssertionError("restore_backup should not run when strict mode is preflight-blocked")

    monkeypatch.setattr(
        "app.routes.auth_routes.restore_backup",
        _restore_should_not_run,
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/strict_blocked.db",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Compatibility warnings:" in response.data
    assert b"Strict restore blocked by preflight data-quality findings." in response.data
    assert b"purchase_invoice_draft" in response.data
    assert b"Backup restored from strict_blocked.db" not in response.data
    assert restore_called["value"] is False


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
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/controlpanel/backups")
    follow_up = client.get("/controlpanel/backups", follow_redirects=True)

    assert follow_up.status_code == 200
    assert b"Favorites mode: pruned invalid favorites." in follow_up.data

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
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/controlpanel/backups")
    follow_up = client.get("/controlpanel/backups", follow_redirects=True)

    assert follow_up.status_code == 200
    assert (
        b"Favorites mode: ignored backup favorites and cleared all user favorites."
        in follow_up.data
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


def test_backup_page_hides_manage_actions_for_view_only_users(client, app):
    backup_filename = "view_only_backup.db"
    _create_sqlite_backup(app, backup_filename)

    with app.app_context():
        user = User(
            email="backup-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "backups.view",
            group_name="Backup View Only",
            description="Can only view the backup page.",
        )

    with client:
        login(client, "backup-viewer@example.com", "pass")
        response = client.get("/controlpanel/backups", follow_redirects=True)

    assert response.status_code == 200
    assert b"You have read-only backup access." in response.data
    assert backup_filename.encode() in response.data
    assert b"Create Backup" not in response.data
    assert b'class="form-control-file"' not in response.data
    assert b'btn btn-sm btn-danger">Restore<' not in response.data
    assert b'btn btn-sm btn-secondary ms-1">Download<' not in response.data


def test_backup_page_lists_only_database_files(client, app):
    backup_filename = "listed_backup.db"
    _create_sqlite_backup(app, backup_filename)
    diagnostic_path = os.path.join(
        app.config["BACKUP_FOLDER"], "restore_preflight_diag_deadbeef.json"
    )
    with open(diagnostic_path, "w", encoding="utf-8") as handle:
        handle.write("{}")

    with client:
        login(
            client,
            os.getenv("ADMIN_EMAIL", "admin@example.com"),
            os.getenv("ADMIN_PASS", "adminpass"),
        )
        response = client.get("/controlpanel/backups", follow_redirects=True)

    assert response.status_code == 200
    assert backup_filename.encode() in response.data
    assert b"restore_preflight_diag_deadbeef.json" not in response.data


def test_download_backup_rejects_path_traversal(client, app):
    with client:
        login(
            client,
            os.getenv("ADMIN_EMAIL", "admin@example.com"),
            os.getenv("ADMIN_PASS", "adminpass"),
        )
        response = client.get(
            "/controlpanel/backups/download/../outside.db",
            follow_redirects=False,
        )

    assert response.status_code == 404


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
