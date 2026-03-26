import importlib


def test_activity_logger_reexports(monkeypatch):
    """Ensure app.activity_logger forwards to app.utils.activity."""
    called = {}

    def fake_log_activity(message):
        called["message"] = message

    monkeypatch.setattr("app.utils.activity.log_activity", fake_log_activity)
    module = importlib.reload(importlib.import_module("app.activity_logger"))
    module.log_activity("hello")
    assert called["message"] == "hello"


def test_backup_utils_reexports(monkeypatch):
    """Ensure app.backup_utils forwards to app.utils.backup."""
    create_called = {}
    restore_called = {}
    monkeypatch.setattr(
        "app.utils.backup.create_backup",
        lambda: create_called.setdefault("called", True),
    )
    monkeypatch.setattr(
        "app.utils.backup.restore_backup",
        lambda: restore_called.setdefault("called", True),
    )
    module = importlib.reload(importlib.import_module("app.backup_utils"))
    module.create_backup()
    module.restore_backup()
    assert create_called["called"]
    assert restore_called["called"]
