"""Database backup and restore utilities."""

import logging
import os
import tempfile
import time
from dataclasses import dataclass
from contextlib import suppress
from datetime import datetime
from threading import Event, Thread

from flask import current_app
from sqlalchemy import MetaData, Table, create_engine, inspect
from sqlalchemy.exc import DBAPIError, DataError, IntegrityError

from app import db
from app.models import Setting
from app.utils.activity import log_activity

BACKUP_SCHEMA_VERSION = "2026.03"


class RestoreBackupError(RuntimeError):
    """Raised when a restore fails during runtime schema rebuild."""


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

    backup_engine = create_engine(f"sqlite+pysqlite:///{file_path}")
    with backup_engine.connect() as conn:
        inspector = inspect(conn)
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
        if "setting" not in existing_tables:
            warnings.append("Missing setting table.")

        if "setting" in existing_tables:
            marker_query = db.text(
                "SELECT value FROM setting WHERE name = :name LIMIT 1"
            )
            marker = conn.execute(
                marker_query,
                {"name": "APP_SCHEMA_VERSION"},
            ).mappings().first()
            marker_value = "" if marker is None else (marker.get("value") or "")
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
                row["name"]
                for row in conn.execute(db.text("SELECT name FROM setting")).mappings()
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
    filename = f"backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.db"
    backup_path = os.path.join(backups_dir, filename)
    fd, temp_path = tempfile.mkstemp(
        dir=backups_dir, prefix="tmp_backup_", suffix=".db"
    )
    os.close(fd)

    db.session.commit()
    db.engine.dispose()
    try:
        backup_engine = create_engine(f"sqlite+pysqlite:///{temp_path}")
        backup_metadata = MetaData()

        with backup_engine.begin() as backup_conn:
            for table in db.metadata.sorted_tables:
                table_copy = Table(
                    table.name,
                    backup_metadata,
                    *[column.copy() for column in table.columns],
                )
                table_copy.create(bind=backup_conn, checkfirst=True)

                rows = db.session.execute(table.select()).mappings().all()
                if rows:
                    backup_conn.execute(table_copy.insert(), [dict(row) for row in rows])

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
    "RestoreBackupError",
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

    backup_engine = create_engine(f"sqlite+pysqlite:///{file_path}")
    backup_metadata = MetaData()
    backup_metadata.reflect(bind=backup_engine)
    backup_inspector = inspect(backup_engine)

    # Reset current session and rebuild schema based on models
    db.session.remove()

    logger = current_app.logger if current_app else logging.getLogger(__name__)

    with db.engine.begin() as target_conn:
        try:
            db.metadata.drop_all(bind=target_conn)
            db.metadata.create_all(bind=target_conn)
        except Exception as exc:
            raise RestoreBackupError(
                "Restore failed while rebuilding database schema. "
                "The backup may be incompatible with current schema/constraints."
            ) from exc

        # Iterate over all tables in dependency order
        for table in db.metadata.sorted_tables:
            table_name = table.name
            if table_name not in backup_metadata.tables:
                logger.info("Table %s missing from backup", table_name)
                continue

            backup_cols = {
                column["name"] for column in backup_inspector.get_columns(table_name)
            }

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

            select_cols = [c for c in table.columns if c.name in backup_cols]
            backup_table = backup_metadata.tables[table_name]
            with backup_engine.connect() as backup_conn:
                rows = backup_conn.execute(
                    backup_table.select().with_only_columns(
                        *(backup_table.c[c.name] for c in select_cols)
                    )
                ).mappings().all()

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
                try:
                    target_conn.execute(table.insert(), insert_rows)
                except IntegrityError as exc:
                    raise RestoreBackupError(
                        "Restore failed while inserting rows into table "
                        f"'{table_name}' due to a constraint failure "
                        "(for example: foreign key, unique, or not-null). "
                        "Please verify backup data integrity and schema compatibility "
                        "before retrying."
                    ) from exc
                except (DataError, DBAPIError) as exc:
                    raise RestoreBackupError(
                        "Restore failed while inserting rows into table "
                        f"'{table_name}'. This likely indicates a column length/type "
                        "mismatch or driver-level database error while loading backup "
                        "data. Please run the latest database migrations and retry."
                    ) from exc
