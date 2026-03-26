"""Database backup and restore utilities."""

import logging
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from contextlib import suppress
from datetime import datetime
from threading import Event, Thread

from flask import current_app
from sqlalchemy import inspect

from app import db
from app.models import Setting
from app.utils.activity import log_activity

BACKUP_SCHEMA_VERSION = "2026.03"


@dataclass
class RestoreCompatibilityResult:
    compatible: bool
    issues: list[str]
    warnings: list[str] | None = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def _collect_missing_expected_endpoints() -> list[str]:
    """Return missing endpoint issues for enabled restore expectations."""

    issues: list[str] = []
    endpoint_expectations = current_app.config.get("RESTORE_ENDPOINT_EXPECTATIONS", [])
    for expectation in endpoint_expectations:
        module_name = expectation.get("module", "unknown")
        enabled = expectation.get("enabled", True)
        endpoints = expectation.get("endpoints", [])
        if not enabled:
            continue
        missing_endpoints = [
            endpoint
            for endpoint in endpoints
            if endpoint not in current_app.view_functions
        ]
        if missing_endpoints:
            issues.append(
                f"Enabled module '{module_name}' is missing expected endpoints: "
                + ", ".join(missing_endpoints)
                + "."
            )
    return issues


def ensure_backup_schema_marker() -> str:
    """Ensure the DB has a schema marker used for backup compatibility checks."""

    setting = Setting.query.filter_by(name="APP_SCHEMA_VERSION").first()
    if setting is None:
        setting = Setting(name="APP_SCHEMA_VERSION")
        db.session.add(setting)
    if setting.value != BACKUP_SCHEMA_VERSION:
        setting.value = BACKUP_SCHEMA_VERSION
        db.session.commit()
    return setting.value or BACKUP_SCHEMA_VERSION


def validate_restored_backup_compatibility() -> RestoreCompatibilityResult:
    """Validate whether the currently restored DB is compatible with this app."""

    issues: list[str] = []

    marker = Setting.query.filter_by(name="APP_SCHEMA_VERSION").first()
    if marker is None or not (marker.value or "").strip():
        issues.append("Backup is missing APP_SCHEMA_VERSION marker in settings.")
    elif marker.value.strip() != BACKUP_SCHEMA_VERSION:
        issues.append(
            "Backup APP_SCHEMA_VERSION "
            f"{marker.value.strip()} does not match expected {BACKUP_SCHEMA_VERSION}."
        )

    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    required_tables = set(
        current_app.config.get(
            "RESTORE_REQUIRED_TABLES",
            ["setting", "user", "invoice", "transfer"],
        )
    )
    missing_tables = sorted(required_tables - existing_tables)
    if missing_tables:
        issues.append(f"Missing required tables: {', '.join(missing_tables)}.")

    required_feature_flags = current_app.config.get(
        "RESTORE_REQUIRED_FEATURE_FLAGS", []
    )
    missing_feature_flags: list[str] = []
    for setting_name in required_feature_flags:
        if not setting_name:
            continue
        if Setting.query.filter_by(name=setting_name).first() is None:
            missing_feature_flags.append(setting_name)
    if missing_feature_flags:
        issues.append(
            "Missing required feature-flag settings: "
            + ", ".join(sorted(missing_feature_flags))
            + "."
        )

    issues.extend(_collect_missing_expected_endpoints())

    return RestoreCompatibilityResult(compatible=not issues, issues=issues)


def validate_backup_file_compatibility(
    file_path: str,
) -> RestoreCompatibilityResult:
    """Validate whether a backup file is compatible before restore."""

    issues: list[str] = []
    warnings: list[str] = []

    with sqlite3.connect(f"file:{file_path}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row

        existing_tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required_tables = set(
            current_app.config.get(
                "RESTORE_REQUIRED_TABLES",
                ["setting", "user", "invoice", "transfer"],
            )
        )
        missing_tables = sorted(required_tables - existing_tables)
        if missing_tables:
            issues.append(f"Missing required tables: {', '.join(missing_tables)}.")
        if "setting" not in existing_tables:
            warnings.append("Missing setting table.")

        if "setting" in existing_tables:
            cursor = conn.execute(
                "SELECT value FROM setting WHERE name = ? LIMIT 1",
                ("APP_SCHEMA_VERSION",),
            )
            marker = cursor.fetchone()
            marker_value = "" if marker is None else (marker["value"] or "")
            if not marker_value.strip():
                warnings.append(
                    "Backup is missing APP_SCHEMA_VERSION marker in settings."
                )
            elif marker_value.strip() != BACKUP_SCHEMA_VERSION:
                warnings.append(
                    "Backup APP_SCHEMA_VERSION "
                    f"{marker_value.strip()} does not match expected {BACKUP_SCHEMA_VERSION}."
                )
        else:
            warnings.append("Backup is missing APP_SCHEMA_VERSION marker in settings.")

        required_feature_flags = current_app.config.get(
            "RESTORE_REQUIRED_FEATURE_FLAGS", []
        )
        if "setting" in existing_tables:
            existing_settings = {
                row["name"] for row in conn.execute("SELECT name FROM setting")
            }
        else:
            existing_settings = set()
        missing_feature_flags = sorted(
            {
                setting_name
                for setting_name in required_feature_flags
                if setting_name and setting_name not in existing_settings
            }
        )
        if missing_feature_flags:
            warnings.append(
                "Missing required feature-flag settings: "
                + ", ".join(missing_feature_flags)
                + "."
            )

    warnings.extend(_collect_missing_expected_endpoints())

    return RestoreCompatibilityResult(
        compatible=not issues,
        issues=issues,
        warnings=warnings,
    )


UNIT_SECONDS = {
    "hour": 60 * 60,
    "day": 60 * 60 * 24,
    "week": 60 * 60 * 24 * 7,
    "month": 60 * 60 * 24 * 30,
    "year": 60 * 60 * 24 * 365,
}

_backup_thread: Thread | None = None
_stop_event = Event()


def _get_db_path():
    """Return the filesystem path to the database file."""
    db_uri = current_app.config["SQLALCHEMY_DATABASE_URI"]
    if db_uri.startswith("sqlite:///"):
        return db_uri.replace("sqlite:///", "", 1)
    raise RuntimeError("Only sqlite databases are supported")


def create_backup(*, initiated_by_system: bool = False):
    """Create a timestamped copy of the database.

    Parameters
    ----------
    initiated_by_system:
        When ``True`` the activity log will record that the system created a
        backup (as opposed to a user triggered backup).
    """
    ensure_backup_schema_marker()
    backups_dir = current_app.config["BACKUP_FOLDER"]
    os.makedirs(backups_dir, exist_ok=True)
    try:
        os.chmod(backups_dir, 0o777)
    except OSError:
        pass
    logger = current_app.logger if current_app else logging.getLogger(__name__)
    max_backups = current_app.config.get("MAX_BACKUPS")
    files = sorted(f for f in os.listdir(backups_dir) if f.endswith(".db"))
    if max_backups:
        while len(files) >= int(max_backups):
            oldest = files.pop(0)
            try:
                os.remove(os.path.join(backups_dir, oldest))
                if initiated_by_system:
                    log_activity(f"System automatically deleted backup {oldest}")
                logger.info("Deleted oldest backup %s", oldest)
            except OSError:
                logger.warning("Failed to delete backup %s", oldest, exc_info=True)
    db_path = _get_db_path()
    filename = f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = os.path.join(backups_dir, filename)
    fd, temp_path = tempfile.mkstemp(
        dir=backups_dir, prefix="tmp_backup_", suffix=".db"
    )
    os.close(fd)

    db.session.commit()
    db.engine.dispose()
    try:
        shutil.copyfile(db_path, temp_path)
        os.replace(temp_path, backup_path)
    except Exception:
        with suppress(OSError):
            os.remove(temp_path)
        raise

    logger.info("Created backup %s", filename)
    if initiated_by_system:
        log_activity(f"System automatically created backup {filename}")

    return filename


def _backup_loop(app, interval: int):
    next_run = time.monotonic() + interval
    while True:
        remaining = next_run - time.monotonic()
        if remaining > 0:
            if _stop_event.wait(remaining):
                break
        elif _stop_event.is_set():
            break

        with app.app_context():
            create_backup(initiated_by_system=True)

        next_run += interval
        current_time = time.monotonic()
        while next_run <= current_time:
            next_run += interval


def start_auto_backup_thread(app):
    """Start or restart the automatic backup thread based on app config."""
    global _backup_thread, _stop_event
    if hasattr(app, "_get_current_object"):
        app = app._get_current_object()
    if _backup_thread and _backup_thread.is_alive():
        _stop_event.set()
        _backup_thread.join()
        _stop_event = Event()

    if not app.config.get("AUTO_BACKUP_ENABLED"):
        return

    interval = app.config.get("AUTO_BACKUP_INTERVAL")
    if not interval:
        return
    _backup_thread = Thread(target=_backup_loop, args=(app, interval), daemon=True)
    _backup_thread.start()


__all__ = [
    "BACKUP_SCHEMA_VERSION",
    "create_backup",
    "ensure_backup_schema_marker",
    "restore_backup",
    "start_auto_backup_thread",
    "UNIT_SECONDS",
    "validate_backup_file_compatibility",
    "validate_restored_backup_compatibility",
]


def restore_backup(file_path):
    """Restore the database from the specified file.

    The backup is read using a separate SQLite connection. The current database
    is rebuilt using the models defined in the application. For each table we
    copy rows from the backup, inserting only the columns that exist in the
    current schema and supplying defaults for any new columns.
    """

    # Open the backup file in a separate SQLite connection
    backup_conn = sqlite3.connect(file_path, detect_types=sqlite3.PARSE_DECLTYPES)
    backup_conn.row_factory = sqlite3.Row
    backup_cursor = backup_conn.cursor()

    # Reset current session and rebuild schema based on models
    db.session.remove()
    db.drop_all()
    db.create_all()

    logger = current_app.logger if current_app else logging.getLogger(__name__)

    # Iterate over all tables in dependency order
    for table in db.metadata.sorted_tables:
        table_name = table.name

        # Ensure the table exists in the backup database
        backup_cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if not backup_cursor.fetchone():
            logger.info("Table %s missing from backup", table_name)
            continue

        # Columns present in backup
        quoted_table = f'"{table_name}"'
        backup_cursor.execute(f"PRAGMA table_info({quoted_table})")
        backup_cols = {row[1] for row in backup_cursor.fetchall()}

        current_cols = {c.name for c in table.columns}
        missing_cols = current_cols - backup_cols
        extra_cols = backup_cols - current_cols
        if missing_cols or extra_cols:
            logger.info(
                "Schema mismatch for %s; missing=%s, extra=%s",
                table_name,
                sorted(missing_cols),
                sorted(extra_cols),
            )

        # Only select columns that exist in both schemas
        select_cols = [c for c in table.columns if c.name in backup_cols]
        col_names = ", ".join(f'"{c.name}"' for c in select_cols)
        backup_cursor.execute(f"SELECT {col_names} FROM {quoted_table}")
        rows = backup_cursor.fetchall()

        insert_rows = []
        for row in rows:
            record = {col.name: row[col.name] for col in select_cols}

            for col in table.columns:
                if col.name not in record:
                    default = None
                    if col.default is not None:
                        default = col.default.arg
                        if callable(default):
                            try:
                                default = default()
                            except TypeError:
                                default = default(None)
                    record[col.name] = default
                else:
                    value = record[col.name]
                    if isinstance(col.type, db.DateTime) and isinstance(value, str):
                        try:
                            record[col.name] = datetime.fromisoformat(value)
                        except ValueError:
                            pass
                    elif isinstance(col.type, db.Date) and isinstance(value, str):
                        try:
                            record[col.name] = datetime.fromisoformat(value).date()
                        except ValueError:
                            pass

            insert_rows.append(record)

        if insert_rows:
            db.session.execute(table.insert(), insert_rows)

    db.session.commit()

    backup_conn.close()
