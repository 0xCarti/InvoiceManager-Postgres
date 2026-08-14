from pathlib import Path

import pytest

from app.utils.pos_import_security import (
    ensure_pos_import_storage_writable,
    resolve_pos_import_storage_dir,
)


def test_pos_import_storage_defaults_below_upload_folder(tmp_path):
    config = {
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "MAILGUN_INBOUND_STORAGE_DIR": "",
    }

    assert resolve_pos_import_storage_dir(config) == (
        tmp_path / "uploads" / "mailgun_inbound"
    )


def test_pos_import_storage_honors_explicit_directory(tmp_path):
    configured_dir = tmp_path / "custom" / "sales-imports"

    assert resolve_pos_import_storage_dir(
        {
            "UPLOAD_FOLDER": str(tmp_path / "uploads"),
            "MAILGUN_INBOUND_STORAGE_DIR": str(configured_dir),
        }
    ) == configured_dir


def test_pos_import_storage_probe_creates_directory_and_leaves_no_file(tmp_path):
    storage_dir = tmp_path / "uploads" / "mailgun_inbound"

    resolved = ensure_pos_import_storage_writable(
        {"UPLOAD_FOLDER": str(tmp_path / "uploads")}
    )

    assert resolved == storage_dir
    assert storage_dir.is_dir()
    assert list(storage_dir.iterdir()) == []


def test_pos_import_storage_probe_reports_permission_failure(
    tmp_path, monkeypatch
):
    storage_dir = tmp_path / "mailgun_inbound"

    def _deny_probe(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(
        "app.utils.pos_import_security.tempfile.NamedTemporaryFile",
        _deny_probe,
    )

    with pytest.raises(RuntimeError) as exc_info:
        ensure_pos_import_storage_writable(
            {
                "UPLOAD_FOLDER": str(tmp_path / "uploads"),
                "MAILGUN_INBOUND_STORAGE_DIR": str(storage_dir),
            }
        )

    message = str(exc_info.value)
    assert str(storage_dir) in message
    assert "application user can create and delete files" in message


def test_pos_import_storage_probe_rejects_regular_file(tmp_path):
    storage_path = tmp_path / "mailgun_inbound"
    storage_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(RuntimeError, match="not writable"):
        ensure_pos_import_storage_writable(
            {
                "UPLOAD_FOLDER": str(tmp_path / "uploads"),
                "MAILGUN_INBOUND_STORAGE_DIR": str(storage_path),
            }
        )

    assert storage_path.read_text(encoding="utf-8") == "not a directory"


def test_create_app_refuses_unwritable_pos_import_storage(tmp_path, monkeypatch):
    from app import create_app

    storage_path = tmp_path / "mailgun_inbound"
    storage_path.write_text("not a directory", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SQLALCHEMY_DATABASE_URI", "sqlite://")
    monkeypatch.setenv("SQLALCHEMY_USE_NULL_POOL", "1")
    monkeypatch.setenv("SKIP_DB_CREATE_ALL", "1")
    monkeypatch.setenv("MAILGUN_INBOUND_STORAGE_DIR", str(storage_path))

    with pytest.raises(RuntimeError, match="POS sales import storage directory"):
        create_app(["--demo"])
