"""Version-aware backup restore adapters.

Adapters translate legacy backup table rows into row dictionaries that match
the current SQLAlchemy model schema used by restore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import MetaData, Table


def _parse_marker(marker: str | None) -> tuple[int, int] | None:
    if not marker:
        return None
    raw = marker.strip()
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def _marker_leq(marker: str | None, upper_bound: str) -> bool:
    parsed_marker = _parse_marker(marker)
    parsed_upper = _parse_marker(upper_bound)
    if parsed_marker is None or parsed_upper is None:
        return False
    return parsed_marker <= parsed_upper


@dataclass
class RestoreAdapterContext:
    backup_metadata: MetaData
    schema_marker: str | None


@dataclass
class RestoreAdapterResult:
    rows: list[dict]
    transformed_count: int = 0
    unresolved_rows: list[dict] | None = None
    metrics: dict[str, int] | None = None


class RestoreTableAdapter:
    """Adapter interface for table-level legacy backup transforms."""

    table_name: str

    def applies_to(
        self,
        *,
        table: Table,
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> bool:
        raise NotImplementedError

    def adapt(
        self,
        *,
        table: Table,
        rows: Iterable[dict],
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> RestoreAdapterResult:
        raise NotImplementedError


class PurchaseInvoiceItemLegacyGlCodeAdapter(RestoreTableAdapter):
    """Map legacy `gl_code_id` to `purchase_gl_code_id` for invoice items."""

    table_name = "purchase_invoice_item"
    _legacy_upper_marker = "2025.12"

    def applies_to(
        self,
        *,
        table: Table,
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> bool:
        if table.name != self.table_name:
            return False
        if "gl_code_id" not in backup_columns:
            return False
        if "purchase_gl_code_id" in backup_columns:
            return False
        # Schema signature is primary signal; marker match is secondary support.
        marker = context.schema_marker
        return marker is None or _marker_leq(marker, self._legacy_upper_marker)

    def adapt(
        self,
        *,
        table: Table,
        rows: Iterable[dict],
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> RestoreAdapterResult:
        adapted_rows: list[dict] = []
        remapped = 0
        for row in rows:
            record = dict(row)
            if "purchase_gl_code_id" not in record and "gl_code_id" in record:
                record["purchase_gl_code_id"] = record.get("gl_code_id")
                remapped += 1
            record.pop("gl_code_id", None)
            adapted_rows.append(record)
        return RestoreAdapterResult(
            rows=adapted_rows,
            transformed_count=remapped,
            metrics={"legacy_gl_code_id_remapped": remapped},
        )


RESTORE_TABLE_ADAPTERS: tuple[RestoreTableAdapter, ...] = (
    PurchaseInvoiceItemLegacyGlCodeAdapter(),
)


def apply_restore_adapters(
    *,
    table: Table,
    backup_columns: set[str],
    rows: list[dict],
    context: RestoreAdapterContext,
) -> RestoreAdapterResult:
    adapted_rows = list(rows)
    transformed_count = 0
    metrics: dict[str, int] = {}
    unresolved_rows: list[dict] = []

    for adapter in RESTORE_TABLE_ADAPTERS:
        if not adapter.applies_to(
            table=table,
            backup_columns=backup_columns,
            context=context,
        ):
            continue
        result = adapter.adapt(
            table=table,
            rows=adapted_rows,
            backup_columns=backup_columns,
            context=context,
        )
        adapted_rows = result.rows
        transformed_count += result.transformed_count
        for key, value in (result.metrics or {}).items():
            metrics[key] = metrics.get(key, 0) + value
        unresolved_rows.extend(result.unresolved_rows or [])

    return RestoreAdapterResult(
        rows=adapted_rows,
        transformed_count=transformed_count,
        unresolved_rows=unresolved_rows,
        metrics=metrics or None,
    )
