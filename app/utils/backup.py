"""Database backup and restore utilities."""

import json
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
from sqlalchemy.sql.schema import ColumnCollectionConstraint
from sqlalchemy.exc import DBAPIError, DataError, IntegrityError

from app import db
from app.models import Setting
from app.utils.activity import log_activity
from app.utils.restore_adapters import (
    RestoreAdapterContext,
    apply_restore_adapters,
)

BACKUP_SCHEMA_VERSION = "2026.03"


class RestoreBackupError(RuntimeError):
    """Raised when a restore fails during runtime schema rebuild."""


def _truncate_value(value, max_length: int = 80) -> str:
    """Return a compact string representation for diagnostic messages."""

    text = str(value)
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 3]}..."


def _extract_dbapi_diagnostics(error) -> dict[str, str | None]:
    """Extract common DBAPI diagnostic fields from wrapped driver errors."""

    details: dict[str, str | None] = {
        "error_type": type(error).__name__,
        "sqlstate": getattr(error, "pgcode", None) or getattr(error, "sqlstate", None),
        "constraint": None,
        "table": None,
        "column": None,
        "detail": None,
    }

    diag = getattr(error, "diag", None)
    if diag is not None:
        details["constraint"] = getattr(diag, "constraint_name", None)
        details["table"] = getattr(diag, "table_name", None)
        details["column"] = getattr(diag, "column_name", None)
        details["detail"] = getattr(diag, "message_detail", None)

    details["constraint"] = details["constraint"] or getattr(
        error, "constraint_name", None
    )
    details["table"] = details["table"] or getattr(error, "table_name", None)
    details["column"] = details["column"] or getattr(error, "column_name", None)
    details["detail"] = details["detail"] or getattr(error, "detail", None)
    return details


def _summarize_offending_row(row: dict, limit: int = 3) -> dict[str, str]:
    """Return a tiny identifier-only sample from a failing insert row."""

    preferred_keys = ("id", "uuid", "email", "name", "code", "invoice_number")
    selected: dict[str, str] = {}

    for key in preferred_keys:
        if key in row and row.get(key) is not None:
            selected[key] = _truncate_value(row.get(key))
        if len(selected) >= limit:
            return selected

    for key, value in row.items():
        if value is None:
            continue
        selected[key] = _truncate_value(value)
        if len(selected) >= limit:
            break
    return selected


@dataclass
class RestoreCompatibilityResult:
    compatible: bool
    issues: list[str]
    warnings: list[str] | None = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


@dataclass
class RestoreSummary:
    mode: str
    inserted_count: int
    skipped_count: int
    affected_tables: list[str]
    quarantine_report: str | None = None
    repaired_count: int = 0
    repair_report: dict[str, dict[str, int]] | None = None
    table_transform_counts: dict[str, int] | None = None
    field_transform_counts: dict[str, dict[str, int]] | None = None


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
    strict_mode = bool(
        current_app.config.get("RESTORE_PREFLIGHT_STRICT_FK_VALIDATION", False)
    )

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

        fk_findings = _collect_foreign_key_orphan_findings(inspector, conn)
        if fk_findings:
            if strict_mode:
                issues.extend(fk_findings)
            else:
                warnings.extend(fk_findings)

        model_constraint_findings = _collect_model_constraint_findings(conn)
        if model_constraint_findings:
            if strict_mode:
                issues.extend(model_constraint_findings)
            else:
                warnings.extend(model_constraint_findings)

    warnings.extend(_collect_missing_expected_endpoints())

    return RestoreCompatibilityResult(
        compatible=not issues,
        issues=issues,
        warnings=warnings,
    )


def _quote_identifier(name: str) -> str:
    """Return a SQLite-safe quoted identifier."""
    return f'"{name.replace(chr(34), chr(34) * 2)}"'


def _collect_foreign_key_orphan_findings(inspector, conn) -> list[str]:
    """Return compatibility findings for orphaned FK rows in backup tables."""

    findings: list[str] = []
    table_names = sorted(inspector.get_table_names())

    for table_name in table_names:
        for fk in inspector.get_foreign_keys(table_name):
            constrained_columns = list(fk.get("constrained_columns") or [])
            referred_table = fk.get("referred_table")
            referred_columns = list(fk.get("referred_columns") or [])
            fk_name = fk.get("name") or "<unnamed>"
            fk_label = f"{table_name}.{fk_name}"

            if not constrained_columns or not referred_table or not referred_columns:
                findings.append(
                    f"Foreign key {fk_label} has incomplete metadata and could not be validated."
                )
                continue

            if len(constrained_columns) != len(referred_columns):
                findings.append(
                    f"Foreign key {fk_label} column mismatch: "
                    f"{len(constrained_columns)} child column(s) vs "
                    f"{len(referred_columns)} parent column(s)."
                )
                continue

            if referred_table not in table_names:
                findings.append(
                    f"Foreign key {fk_label} references missing parent table '{referred_table}'."
                )
                continue

            child_alias = "child_row"
            parent_alias = "parent_row"
            join_conditions = " AND ".join(
                f'{child_alias}.{_quote_identifier(child_col)} = '
                f'{parent_alias}.{_quote_identifier(parent_col)}'
                for child_col, parent_col in zip(
                    constrained_columns, referred_columns, strict=True
                )
            )
            child_not_null = " AND ".join(
                f"{child_alias}.{_quote_identifier(child_col)} IS NOT NULL"
                for child_col in constrained_columns
            )
            parent_missing = " AND ".join(
                f"{parent_alias}.{_quote_identifier(parent_col)} IS NULL"
                for parent_col in referred_columns
            )
            selected_values = ", ".join(
                f"{child_alias}.{_quote_identifier(child_col)} AS {_quote_identifier(child_col)}"
                for child_col in constrained_columns
            )

            from_join = (
                f"FROM {_quote_identifier(table_name)} AS {child_alias} "
                f"LEFT JOIN {_quote_identifier(referred_table)} AS {parent_alias} "
                f"ON {join_conditions}"
            )
            where_clause = f"WHERE {child_not_null} AND {parent_missing}"

            orphan_count = conn.execute(
                db.text(f"SELECT COUNT(*) {from_join} {where_clause}")
            ).scalar_one()
            if not orphan_count:
                continue

            sample_rows = conn.execute(
                db.text(
                    f"SELECT {selected_values} {from_join} {where_clause} "
                    "ORDER BY rowid LIMIT 3"
                )
            ).mappings().all()
            sample_text = ", ".join(
                str({column: row.get(column) for column in constrained_columns})
                for row in sample_rows
            )
            findings.append(
                f"Foreign key orphan rows found for {fk_label} -> {referred_table} "
                f"({orphan_count} row(s)). Sample key values: {sample_text}."
            )

    return findings


def _collect_model_constraint_findings(conn) -> list[str]:
    """Return findings for backup rows that violate current model constraints."""

    findings: list[str] = []
    inspector = inspect(conn)
    backup_columns_by_table = {
        table_name: {
            column.get("name")
            for column in inspector.get_columns(table_name)
            if column.get("name")
        }
        for table_name in inspector.get_table_names()
    }

    for table in db.metadata.sorted_tables:
        backup_columns = backup_columns_by_table.get(table.name)
        if backup_columns is None:
            continue

        findings.extend(_collect_not_null_findings(conn, table, backup_columns))
        findings.extend(_collect_unique_findings(conn, table, backup_columns))

    return findings


def _collect_not_null_findings(
    conn,
    table: Table,
    backup_columns: set[str],
) -> list[str]:
    findings: list[str] = []
    table_name = _quote_identifier(table.name)
    for column in table.columns:
        if column.nullable:
            continue
        if column.name not in backup_columns:
            findings.append(
                f"Not-null validation deferred for {table.name}.{column.name}: "
                "column absent in backup; deferred to transform/default mapping."
            )
            continue

        column_name = _quote_identifier(column.name)
        violation_count = conn.execute(
            db.text(
                f"SELECT COUNT(*) FROM {table_name} "
                f"WHERE {column_name} IS NULL"
            )
        ).scalar_one()
        if not violation_count:
            continue

        sample_rows = conn.execute(
            db.text(
                f"SELECT rowid FROM {table_name} "
                f"WHERE {column_name} IS NULL ORDER BY rowid LIMIT 3"
            )
        ).scalars().all()
        findings.append(
            f"Not-null violation in {table.name}.{column.name}: "
            f"{violation_count} row(s) contain NULL values. "
            f"Sample rowids: {sample_rows}."
        )

    return findings


def _collect_unique_findings(
    conn,
    table: Table,
    backup_columns: set[str],
) -> list[str]:
    findings: list[str] = []
    seen_constraints: set[tuple[str, ...]] = set()
    unique_sets: list[tuple[str, tuple[str, ...]]] = []

    primary_key_columns = tuple(column.name for column in table.primary_key.columns)
    if primary_key_columns:
        unique_sets.append(("PRIMARY KEY", primary_key_columns))
        seen_constraints.add(primary_key_columns)

    for constraint in table.constraints:
        if not isinstance(constraint, ColumnCollectionConstraint):
            continue
        if not getattr(constraint, "unique", False):
            continue
        columns = tuple(column.name for column in constraint.columns)
        if not columns or columns in seen_constraints:
            continue
        name = constraint.name or "UNIQUE"
        unique_sets.append((name, columns))
        seen_constraints.add(columns)

    for index in table.indexes:
        if not index.unique:
            continue
        columns = tuple(column.name for column in index.columns)
        if not columns or columns in seen_constraints:
            continue
        unique_sets.append((index.name or "UNIQUE INDEX", columns))
        seen_constraints.add(columns)

    if not unique_sets:
        return findings

    quoted_table = _quote_identifier(table.name)
    for constraint_name, columns in unique_sets:
        missing_columns = [column for column in columns if column not in backup_columns]
        if missing_columns:
            findings.append(
                f"Unique validation deferred for {table.name} on {constraint_name} "
                f"({', '.join(columns)}): column absent in backup "
                f"({', '.join(missing_columns)}); deferred to transform/default mapping."
            )
            continue

        quoted_columns = [_quote_identifier(column) for column in columns]
        not_null_filter = " AND ".join(f"{column} IS NOT NULL" for column in quoted_columns)
        grouped_columns = ", ".join(quoted_columns)

        duplicate_groups_count = conn.execute(
            db.text(
                f"SELECT COUNT(*) FROM ("
                f"SELECT 1 FROM {quoted_table} "
                f"WHERE {not_null_filter} "
                f"GROUP BY {grouped_columns} "
                f"HAVING COUNT(*) > 1"
                f")"
            )
        ).scalar_one()
        if not duplicate_groups_count:
            continue

        sample_rows = conn.execute(
            db.text(
                f"SELECT {grouped_columns}, COUNT(*) AS duplicate_count "
                f"FROM {quoted_table} "
                f"WHERE {not_null_filter} "
                f"GROUP BY {grouped_columns} "
                f"HAVING COUNT(*) > 1 "
                f"ORDER BY duplicate_count DESC LIMIT 3"
            )
        ).mappings().all()
        samples = [
            {
                "values": {column: row.get(column) for column in columns},
                "duplicate_count": row.get("duplicate_count"),
            }
            for row in sample_rows
        ]
        findings.append(
            f"Unique violation for {table.name} on {constraint_name} "
            f"({', '.join(columns)}): {duplicate_groups_count} duplicate key group(s). "
            f"Sample duplicates: {samples}."
        )

    return findings


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
    "RestoreSummary",
    "check_and_fix_pk_sequences",
    "create_backup",
    "ensure_backup_schema_marker",
    "reconcile_postgres_table_pk_sequence",
    "restore_backup",
    "start_auto_backup_thread",
    "UNIT_SECONDS",
    "validate_backup_file_compatibility",
    "validate_restored_backup_compatibility",
]


def restore_backup(file_path, *, restore_mode: str | None = None):
    """Restore the database from the specified file.

    The backup is read using a separate SQLite connection. The current database
    is rebuilt using the models defined in the application. For each table we
    copy rows from the backup, inserting only the columns that exist in the
    current schema and supplying defaults for any new columns.
    """

    if restore_mode is None:
        restore_mode = current_app.config.get("RESTORE_MODE_DEFAULT", "strict")
    return _restore_backup(file_path, restore_mode=restore_mode)


def _normalize_restore_mode(restore_mode: str | None) -> str:
    mode = (restore_mode or "").strip().lower()
    if mode in {"permissive", "lenient"}:
        return "permissive"
    return "strict"


def _extract_primary_key_values(table: Table, row: dict) -> dict[str, str]:
    values: dict[str, str] = {}
    for column in table.primary_key.columns:
        if column.name in row:
            values[column.name] = str(row.get(column.name))
    return values


def _row_failure_reason(exc: Exception) -> str:
    reason = getattr(exc, "orig", exc)
    return str(reason)[:500]


def _write_quarantine_report(
    *,
    backup_file_path: str,
    skipped_rows: list[dict],
    restore_mode: str,
    table_transform_metrics: dict[str, dict[str, int]] | None = None,
) -> str:
    backups_dir = current_app.config["BACKUP_FOLDER"]
    os.makedirs(backups_dir, exist_ok=True)
    report_name = (
        f"restore_quarantine_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    )
    report_path = os.path.join(backups_dir, report_name)
    payload = {
        "created_at_utc": datetime.utcnow().isoformat(),
        "backup_file": os.path.basename(backup_file_path),
        "restore_mode": restore_mode,
        "skipped_count": len(skipped_rows),
        "skipped_rows": skipped_rows,
        "table_transform_metrics": table_transform_metrics or {},
    }
    with open(report_path, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2, default=str)
    return report_name


def _insert_rows_permissive(
    *,
    target_conn,
    table: Table,
    rows: list[dict],
    skipped_rows: list[dict],
    logger,
) -> int:
    def _insert_with_bisect(batch: list[dict]) -> int:
        if not batch:
            return 0
        try:
            with target_conn.begin_nested():
                target_conn.execute(table.insert(), batch)
            return len(batch)
        except (IntegrityError, DataError, DBAPIError) as exc:
            if len(batch) == 1:
                record = batch[0]
                skipped_rows.append(
                    {
                        "table": table.name,
                        "primary_key": _extract_primary_key_values(table, record),
                        "reason": _row_failure_reason(exc),
                        "row": record,
                    }
                )
                logger.warning(
                    "Skipping invalid restore row for table %s pk=%s reason=%s",
                    table.name,
                    _extract_primary_key_values(table, record),
                    _row_failure_reason(exc),
                )
                return 0

            midpoint = len(batch) // 2
            return _insert_with_bisect(batch[:midpoint]) + _insert_with_bisect(
                batch[midpoint:]
            )

    batch_size = int(current_app.config.get("RESTORE_PERMISSIVE_BATCH_SIZE", 200))
    inserted = 0
    for idx in range(0, len(rows), batch_size):
        batch = rows[idx : idx + batch_size]
        inserted += _insert_with_bisect(batch)
    return inserted


def _load_table_key_set(
    backup_conn,
    *,
    backup_metadata: MetaData,
    table_name: str,
    key_column: str = "id",
) -> set:
    table = backup_metadata.tables.get(table_name)
    if table is None or key_column not in table.c:
        return set()
    rows = backup_conn.execute(
        table.select().with_only_columns(table.c[key_column])
    ).all()
    return {row[0] for row in rows if row and row[0] is not None}


def _normalize_purchase_invoice_draft_rows(
    *,
    rows: list[dict],
    backup_conn,
    backup_metadata: MetaData,
) -> tuple[list[dict], dict[str, int]]:
    purchase_order_ids = _load_table_key_set(
        backup_conn, backup_metadata=backup_metadata, table_name="purchase_order"
    )
    kept_rows: list[dict] = []
    dropped_orphans = 0
    for row in rows:
        purchase_order_id = row.get("purchase_order_id")
        if purchase_order_id is not None and purchase_order_id not in purchase_order_ids:
            dropped_orphans += 1
            continue
        kept_rows.append(row)
    return kept_rows, {"dropped_orphans": dropped_orphans}


def _normalize_purchase_invoice_item_rows(
    *,
    rows: list[dict],
    backup_conn,
    backup_metadata: MetaData,
) -> tuple[list[dict], dict[str, int]]:
    gl_code_ids = _load_table_key_set(
        backup_conn, backup_metadata=backup_metadata, table_name="gl_code"
    )
    repaired = 0
    for row in rows:
        purchase_gl_code_id = row.get("purchase_gl_code_id")
        if purchase_gl_code_id is None:
            continue
        if purchase_gl_code_id not in gl_code_ids:
            row["purchase_gl_code_id"] = None
            repaired += 1
    return rows, {"nullified_orphans": repaired}


RESTORE_ROW_NORMALIZERS: dict[str, list] = {
    "purchase_invoice_draft": [_normalize_purchase_invoice_draft_rows],
    "purchase_invoice_item": [_normalize_purchase_invoice_item_rows],
}


def _read_backup_schema_marker(backup_engine) -> str | None:
    inspector = inspect(backup_engine)
    if "setting" not in inspector.get_table_names():
        return None
    metadata = MetaData()
    metadata.reflect(bind=backup_engine, only=["setting"])
    setting_table = metadata.tables.get("setting")
    if setting_table is None or "name" not in setting_table.c:
        return None
    value_col = setting_table.c.get("value")
    if value_col is None:
        return None

    with backup_engine.connect() as conn:
        marker = conn.execute(
            setting_table.select()
            .with_only_columns(value_col)
            .where(setting_table.c.name == "APP_SCHEMA_VERSION")
            .limit(1)
        ).scalar_one_or_none()
    if marker is None:
        return None
    marker_text = str(marker).strip()
    return marker_text or None


def _is_single_integer_primary_key(table: Table):
    """Return the table's single integer PK column when available."""

    primary_key_columns = list(table.primary_key.columns)
    if len(primary_key_columns) != 1:
        return None
    pk_column = primary_key_columns[0]
    try:
        if pk_column.type.python_type is int:
            return pk_column
    except (NotImplementedError, AttributeError):
        return None
    return None


def reconcile_postgres_table_pk_sequence(
    conn,
    *,
    table_name: str,
    pk_column_name: str = "id",
    logger: logging.Logger | None = None,
) -> bool:
    """Reset a PostgreSQL table PK sequence to the table's current max value."""

    active_logger = logger or logging.getLogger(__name__)
    sequence_name = conn.execute(
        db.text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
        {"table_name": table_name, "column_name": pk_column_name},
    ).scalar_one_or_none()

    if not sequence_name:
        active_logger.info(
            "Skipping sequence reset for %s.%s; no serial/identity sequence found",
            table_name,
            pk_column_name,
        )
        return False

    max_pk_value = conn.execute(
        db.text(f'SELECT COALESCE(MAX("{pk_column_name}"), 1) FROM "{table_name}"')
    ).scalar_one()
    conn.execute(
        db.text("SELECT setval(CAST(:sequence_name AS regclass), :max_value, true)"),
        {"sequence_name": sequence_name, "max_value": max_pk_value},
    )
    active_logger.info(
        "Reset sequence %s for %s.%s to %s",
        sequence_name,
        table_name,
        pk_column_name,
        max_pk_value,
    )
    return True


def check_and_fix_pk_sequences(
    conn,
    *,
    tables: list[Table] | None = None,
    auto_fix: bool = False,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    """Check (and optionally repair) PostgreSQL integer PK sequence drift."""

    active_logger = logger or logging.getLogger(__name__)
    table_list = tables or list(db.metadata.sorted_tables)
    summary = {"checked": 0, "drifted": 0, "fixed": 0, "skipped": 0}

    for table in table_list:
        pk_column = _is_single_integer_primary_key(table)
        if pk_column is None:
            continue

        summary["checked"] += 1
        table_name = table.name
        pk_column_name = pk_column.name

        sequence_name = conn.execute(
            db.text("SELECT pg_get_serial_sequence(:table_name, :column_name)"),
            {"table_name": table_name, "column_name": pk_column_name},
        ).scalar_one_or_none()

        if not sequence_name:
            summary["skipped"] += 1
            active_logger.info(
                "PK sequence check skipped for %s.%s; no serial/identity sequence found",
                table_name,
                pk_column_name,
            )
            continue

        max_pk_value = conn.execute(
            db.text(f'SELECT COALESCE(MAX("{pk_column_name}"), 0) FROM "{table_name}"')
        ).scalar_one()
        sequence_state = conn.execute(
            db.text(f"SELECT last_value, is_called FROM {sequence_name}")
        ).first()
        if sequence_state is None:
            summary["skipped"] += 1
            active_logger.warning(
                "PK sequence check skipped for %s.%s; unable to read sequence state for %s",
                table_name,
                pk_column_name,
                sequence_name,
            )
            continue

        sequence_last_value, sequence_is_called = sequence_state
        next_sequence_value = (
            int(sequence_last_value) + 1 if sequence_is_called else int(sequence_last_value)
        )
        is_drifted = next_sequence_value <= int(max_pk_value)

        if is_drifted:
            summary["drifted"] += 1
            if auto_fix:
                conn.execute(
                    db.text(
                        "SELECT setval(CAST(:sequence_name AS regclass), :max_value, true)"
                    ),
                    {"sequence_name": sequence_name, "max_value": int(max_pk_value)},
                )
                summary["fixed"] += 1
                active_logger.warning(
                    "PK sequence drift detected+fixed for %s.%s sequence=%s nextval=%s max_id=%s",
                    table_name,
                    pk_column_name,
                    sequence_name,
                    next_sequence_value,
                    max_pk_value,
                )
            else:
                active_logger.warning(
                    "PK sequence drift detected for %s.%s sequence=%s nextval=%s max_id=%s",
                    table_name,
                    pk_column_name,
                    sequence_name,
                    next_sequence_value,
                    max_pk_value,
                )
        else:
            active_logger.info(
                "PK sequence check OK for %s.%s sequence=%s nextval=%s max_id=%s",
                table_name,
                pk_column_name,
                sequence_name,
                next_sequence_value,
                max_pk_value,
            )

    active_logger.info(
        "PK sequence check summary: checked=%s drifted=%s fixed=%s skipped=%s auto_fix=%s",
        summary["checked"],
        summary["drifted"],
        summary["fixed"],
        summary["skipped"],
        auto_fix,
    )
    return summary


def _restore_backup(file_path: str, *, restore_mode: str | None = None) -> RestoreSummary:
    restore_mode = _normalize_restore_mode(restore_mode)
    backup_engine = create_engine(f"sqlite+pysqlite:///{file_path}")
    backup_metadata = MetaData()
    backup_metadata.reflect(bind=backup_engine)
    backup_inspector = inspect(backup_engine)
    backup_schema_marker = _read_backup_schema_marker(backup_engine)

    # Reset current session and rebuild schema based on models
    db.session.remove()

    logger = current_app.logger if current_app else logging.getLogger(__name__)

    inserted_total = 0
    skipped_rows: list[dict] = []
    affected_tables: set[str] = set()
    repair_report: dict[str, dict[str, int]] = {}
    repair_total = 0
    table_transform_counts: dict[str, int] = {}
    field_transform_counts: dict[str, dict[str, int]] = {}
    adapter_transform_metrics: dict[str, dict[str, int]] = {}
    repair_orphans_enabled = bool(current_app.config.get("RESTORE_REPAIR_ORPHANS", True))

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
                table_transform_counts[table_name] = (
                    table_transform_counts.get(table_name, 0) + 1
                )
                table_field_counts = field_transform_counts.setdefault(
                    table_name,
                    {
                        "missing_fields_filled_defaults": 0,
                        "legacy_extra_fields_ignored": 0,
                    },
                )
                table_field_counts["missing_fields_filled_defaults"] += len(missing_cols)
                table_field_counts["legacy_extra_fields_ignored"] += len(extra_cols)

            backup_table = backup_metadata.tables[table_name]
            with backup_engine.connect() as backup_conn:
                rows = backup_conn.execute(
                    backup_table.select()
                ).mappings().all()

                adapter_result = apply_restore_adapters(
                    table=table,
                    backup_columns=backup_cols,
                    rows=[dict(row) for row in rows],
                    context=RestoreAdapterContext(
                        backup_metadata=backup_metadata,
                        schema_marker=backup_schema_marker,
                    ),
                )
                rows = adapter_result.rows
                if adapter_result.transformed_count > 0:
                    table_transform_counts[table_name] = (
                        table_transform_counts.get(table_name, 0)
                        + adapter_result.transformed_count
                    )
                if adapter_result.metrics:
                    table_adapter_metrics = adapter_transform_metrics.setdefault(
                        table_name, {}
                    )
                    for key, value in adapter_result.metrics.items():
                        table_adapter_metrics[key] = (
                            table_adapter_metrics.get(key, 0) + value
                        )
                for unresolved in adapter_result.unresolved_rows or []:
                    skipped_rows.append(
                        {
                            "table": table_name,
                            "primary_key": {},
                            "reason": "adapter_unresolved_row",
                            "row": unresolved,
                        }
                    )

                insert_rows = []
                for row in rows:
                    record = {
                        col.name: row[col.name]
                        for col in table.columns
                        if col.name in row
                    }

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

                if repair_orphans_enabled:
                    normalizers = RESTORE_ROW_NORMALIZERS.get(table_name, [])
                    for normalizer in normalizers:
                        insert_rows, modifications = normalizer(
                            rows=insert_rows,
                            backup_conn=backup_conn,
                            backup_metadata=backup_metadata,
                        )
                        for action, count in (modifications or {}).items():
                            if count <= 0:
                                continue
                            table_report = repair_report.setdefault(table_name, {})
                            table_report[action] = table_report.get(action, 0) + count
                            repair_total += count

            if insert_rows:
                if restore_mode == "permissive":
                    try:
                        inserted = _insert_rows_permissive(
                            target_conn=target_conn,
                            table=table,
                            rows=insert_rows,
                            skipped_rows=skipped_rows,
                            logger=logger,
                        )
                    except Exception as exc:
                        raise RestoreBackupError(
                            "Restore failed while inserting rows into table "
                            f"'{table_name}' in permissive mode."
                        ) from exc
                else:
                    try:
                        target_conn.execute(table.insert(), insert_rows)
                        inserted = len(insert_rows)
                    except IntegrityError as exc:
                        first_row = insert_rows[0] if insert_rows else {}
                        dbapi_details = _extract_dbapi_diagnostics(exc.orig)
                        diagnostic_payload = {
                            "phase": "insert_rows",
                            "table": dbapi_details.get("table") or table_name,
                            "constraint": dbapi_details.get("constraint"),
                            "column": dbapi_details.get("column"),
                            "sqlstate": dbapi_details.get("sqlstate"),
                            "driver_error": dbapi_details.get("error_type"),
                            "detail": _truncate_value(
                                dbapi_details.get("detail") or "", max_length=180
                            ),
                            "sample_row_identifiers": _summarize_offending_row(first_row),
                        }
                        logger.error(
                            "Restore integrity violation diagnostics: %s",
                            json.dumps(diagnostic_payload, sort_keys=True),
                        )
                        concise_detail = ", ".join(
                            [
                                f"constraint={dbapi_details['constraint']}"
                                if dbapi_details.get("constraint")
                                else "",
                                f"column={dbapi_details['column']}"
                                if dbapi_details.get("column")
                                else "",
                                f"detail={_truncate_value(dbapi_details['detail'])}"
                                if dbapi_details.get("detail")
                                else "",
                                (
                                    "keys="
                                    + ",".join(
                                        f"{k}:{v}"
                                        for k, v in diagnostic_payload[
                                            "sample_row_identifiers"
                                        ].items()
                                    )
                                )
                                if diagnostic_payload["sample_row_identifiers"]
                                else "",
                            ]
                        ).strip(", ")
                        raise RestoreBackupError(
                            "Restore failed while inserting rows into table "
                            f"'{diagnostic_payload['table']}' due to integrity violation "
                            f"({dbapi_details.get('error_type', 'IntegrityError')}). "
                            f"{concise_detail or 'No additional DBAPI diagnostics available.'}"
                        ) from exc
                    except (DataError, DBAPIError) as exc:
                        raise RestoreBackupError(
                            "Restore failed while inserting rows into table "
                            f"'{table_name}'. This likely indicates a column length/type "
                            "mismatch or driver-level database error while loading backup "
                            "data. Please run the latest database migrations and retry."
                        ) from exc
                    except Exception as exc:
                        raise RestoreBackupError(
                            "Restore failed while inserting rows into table "
                            f"'{table_name}' due to an unexpected error."
                        ) from exc

                if inserted:
                    inserted_total += inserted
                    affected_tables.add(table_name)

        if target_conn.dialect.name == "postgresql":
            logger.info("Running post-restore PK sequence drift check for PostgreSQL")
            check_and_fix_pk_sequences(
                target_conn,
                tables=list(db.metadata.sorted_tables),
                auto_fix=True,
                logger=logger,
            )

    quarantine_report = None
    if skipped_rows:
        affected_tables.update(row["table"] for row in skipped_rows if row.get("table"))
        quarantine_report = _write_quarantine_report(
            backup_file_path=file_path,
            skipped_rows=skipped_rows,
            restore_mode=restore_mode,
            table_transform_metrics=adapter_transform_metrics,
        )

    return RestoreSummary(
        mode=restore_mode,
        inserted_count=inserted_total,
        skipped_count=len(skipped_rows),
        affected_tables=sorted(affected_tables),
        quarantine_report=quarantine_report,
        repaired_count=repair_total,
        repair_report=repair_report or None,
        table_transform_counts=table_transform_counts or None,
        field_transform_counts=field_transform_counts or None,
    )
