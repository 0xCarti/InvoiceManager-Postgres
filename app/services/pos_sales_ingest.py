"""POS sales ingestion helpers for webhook and poll-based imports."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app.models import (
    Location,
    PosSalesImport,
    PosSalesImportLocation,
    PosSalesImportRow,
    Product,
    Setting,
    TerminalSaleLocationAlias,
    TerminalSaleProductAlias,
    db,
)
from app.utils.activity import log_activity
from app.utils.numeric import coerce_float
from app.utils.pos_import import (
    combine_terminal_sales_totals,
    derive_terminal_sales_quantity,
    extract_terminal_sales_location,
    group_terminal_sales_rows,
    iter_pos_excel_rows,
    normalize_pos_alias,
    parse_terminal_sales_email_rows,
    parse_terminal_sales_number,
    terminal_sales_cell_is_blank,
)


def _get_pos_sales_import_interval() -> tuple[int, str]:
    """Return the configured lookback interval for POS sales imports."""

    config_value = current_app.config.get("POS_SALES_IMPORT_INTERVAL_VALUE")
    config_unit = current_app.config.get("POS_SALES_IMPORT_INTERVAL_UNIT")
    if config_value is not None and config_unit in Setting.POS_SALES_IMPORT_INTERVAL_UNITS:
        try:
            cleaned_value = int(config_value)
        except (TypeError, ValueError):
            cleaned_value = 0
        if cleaned_value >= 1:
            return cleaned_value, str(config_unit)

    interval = Setting.get_pos_sales_import_interval()
    return int(interval["value"]), str(interval["unit"])


def _default_sales_import_date(received_at: datetime | None = None):
    """Infer the business date for an imported POS sales file."""

    tz_name = current_app.config.get("DEFAULT_TIMEZONE") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    interval_value, interval_unit = _get_pos_sales_import_interval()
    reference_time = received_at or datetime.now(dt_timezone.utc)
    if reference_time.tzinfo is None:
        reference_time = reference_time.replace(tzinfo=dt_timezone.utc)
    local_reference = reference_time.astimezone(tz)
    delta_kwargs = {f"{interval_unit}s": interval_value}
    return (local_reference - timedelta(**delta_kwargs)).date()


def _parse_rows(filepath: str, extension: str) -> list[dict]:
    """Return normalized row dictionaries from an uploaded POS spreadsheet."""

    parsed_locations = parse_terminal_sales_email_rows(
        iter_pos_excel_rows(filepath, extension)
    )
    if parsed_locations:
        normalized_rows: list[dict] = []
        for location_name, payload in parsed_locations.items():
            for summary in payload.get("location_totals", []):
                normalized_rows.append(
                    {
                        "location": location_name,
                        "is_location_total": True,
                        "quantity": float(summary.get("quantity", 0.0) or 0.0),
                        "net_including_tax_total": float(
                            summary.get("net_inc", 0.0) or 0.0
                        ),
                        "discount_total": float(
                            summary.get("discount_raw", 0.0) or 0.0
                        ),
                        "amount": float(summary.get("line_total", 0.0) or 0.0),
                        "line_total": float(summary.get("line_total", 0.0) or 0.0),
                        "raw_row": summary.get("raw_row"),
                    }
                )
            for row in payload.get("rows", []):
                normalized_rows.append(
                    {
                        "location": location_name,
                        "product": row.get("source_product_name"),
                        "quantity": float(row.get("quantity", 0.0) or 0.0),
                        "price": float(row.get("unit_price", 0.0) or 0.0),
                        "amount": float(row.get("line_total", 0.0) or 0.0),
                        "net_including_tax_total": float(
                            row.get("net_inc", 0.0) or 0.0
                        ),
                        "discount_total": float(row.get("discount_raw", 0.0) or 0.0),
                        "source_product_code": row.get("source_product_code"),
                        "line_total": float(row.get("line_total", 0.0) or 0.0),
                        "raw_row": row.get("raw_row"),
                    }
                )
        return normalized_rows

    rows: list[dict] = []
    current_loc: str | None = None

    for row in iter_pos_excel_rows(filepath, extension):
        location_name = extract_terminal_sales_location(row)
        if location_name:
            current_loc = location_name
            continue

        if not current_loc:
            continue

        second = row[1] if len(row) > 1 else None
        first_cell = row[0] if row else None
        quantity_cell = row[4] if len(row) > 4 else None
        amount_cell = row[5] if len(row) > 5 else None
        net_cell = row[7] if len(row) > 7 else None
        discount_cell = row[8] if len(row) > 8 else None

        summary_quantity = parse_terminal_sales_number(quantity_cell)
        summary_amount = parse_terminal_sales_number(amount_cell)
        summary_net = parse_terminal_sales_number(net_cell)
        summary_discount = parse_terminal_sales_number(discount_cell)

        if (
            terminal_sales_cell_is_blank(first_cell)
            and not isinstance(second, str)
            and (
                summary_quantity is not None
                or summary_amount is not None
                or summary_net is not None
                or summary_discount is not None
            )
        ):
            entry = {"location": current_loc, "is_location_total": True}
            if summary_quantity is not None:
                entry["quantity"] = summary_quantity
            if summary_amount is not None:
                entry["amount"] = summary_amount
            if summary_net is not None:
                entry["net_including_tax_total"] = summary_net
            if summary_discount is not None:
                entry["discount_total"] = summary_discount
            rows.append(entry)
            continue

        if not isinstance(second, str):
            continue

        quantity_value = summary_quantity
        price_cell = row[2] if len(row) > 2 else None

        combined_total_value = combine_terminal_sales_totals(
            summary_net, summary_discount
        )
        computed_price = None
        if (
            combined_total_value is not None
            and quantity_value is not None
            and abs(quantity_value) > 1e-9
        ):
            try:
                computed_price = float(combined_total_value) / float(quantity_value)
            except (TypeError, ValueError, ZeroDivisionError):
                computed_price = None

        price_value = parse_terminal_sales_number(
            computed_price if computed_price is not None else price_cell
        )
        raw_price_value = parse_terminal_sales_number(price_cell)
        amount_value = parse_terminal_sales_number(amount_cell)
        quantity_value = derive_terminal_sales_quantity(
            quantity_value,
            price=price_value,
            amount=amount_value,
            net_including_tax_total=summary_net,
            discounts_total=summary_discount,
        )

        if quantity_value is None:
            continue

        entry = {
            "location": current_loc,
            "product": second.strip(),
            "quantity": quantity_value,
        }
        if price_value is not None:
            entry["price"] = price_value
        if raw_price_value is not None:
            entry["raw_price"] = raw_price_value
        if amount_value is not None:
            entry["amount"] = amount_value
        if summary_net is not None:
            entry["net_including_tax_total"] = summary_net
        if summary_discount is not None:
            entry["discount_total"] = summary_discount
        rows.append(entry)

    return rows


def stage_pos_sales_import(
    pos_import: PosSalesImport, filepath: str, extension: str
) -> None:
    """Parse spreadsheet and persist normalized staging rows for ``pos_import``."""

    parsed_rows = _parse_rows(filepath, extension)
    grouped = group_terminal_sales_rows(parsed_rows)

    location_aliases = {
        alias.normalized_name: alias.location_id
        for alias in TerminalSaleLocationAlias.query.all()
    }
    product_aliases = {
        alias.normalized_name: alias.product_id
        for alias in TerminalSaleProductAlias.query.all()
    }
    exact_location_by_name = {
        (location.name or "").strip().casefold(): location.id
        for location in Location.query.all()
        if location.name
    }
    exact_product_by_name = {
        (product.name or "").strip().casefold(): product.id
        for product in Product.query.all()
        if product.name
    }
    location_by_name = {
        normalize_pos_alias(location.name or ""): location.id
        for location in Location.query.all()
        if location.name
    }
    product_by_name = {
        normalize_pos_alias(product.name or ""): product.id
        for product in Product.query.all()
        if product.name
    }

    location_records: dict[str, PosSalesImportLocation] = {}
    for loc_index, (location_name, payload) in enumerate(grouped.items()):
        normalized_location = normalize_pos_alias(location_name)
        location_id = exact_location_by_name.get(
            (location_name or "").strip().casefold()
        )
        if location_id is None:
            location_id = location_aliases.get(normalized_location)
        if location_id is None:
            location_id = location_by_name.get(normalized_location)

        location_record = PosSalesImportLocation(
            sales_import=pos_import,
            source_location_name=location_name,
            normalized_location_name=normalized_location,
            location_id=location_id,
            total_quantity=coerce_float(payload.get("total"), default=0.0) or 0.0,
            net_inc=coerce_float(payload.get("net_including_tax_total"), default=0.0)
            or 0.0,
            discounts_abs=abs(
                coerce_float(payload.get("discount_total"), default=0.0) or 0.0
            ),
            computed_total=coerce_float(payload.get("total_amount"), default=0.0)
            or 0.0,
            parse_index=loc_index,
        )
        db.session.add(location_record)
        location_records[location_name] = location_record

    row_index_by_location: dict[str, int] = {name: 0 for name in location_records}
    for entry in parsed_rows:
        if entry.get("is_location_total"):
            continue

        location_name = entry.get("location")
        location_record = location_records.get(location_name)
        if location_record is None:
            continue

        product_name = (entry.get("product") or "").strip()
        normalized_product = normalize_pos_alias(product_name)
        product_id = exact_product_by_name.get((product_name or "").strip().casefold())
        if product_id is None:
            product_id = product_aliases.get(normalized_product)
        if product_id is None:
            product_id = product_by_name.get(normalized_product)

        quantity = coerce_float(entry.get("quantity"), default=0.0) or 0.0
        net_inc = coerce_float(entry.get("net_including_tax_total"), default=0.0) or 0.0
        discount_raw = entry.get("discount_total")
        discount_value = coerce_float(discount_raw, default=0.0) or 0.0
        line_total = combine_terminal_sales_totals(net_inc, discount_value)
        if line_total is None:
            line_total = coerce_float(entry.get("amount"), default=0.0) or 0.0
        explicit_line_total = coerce_float(entry.get("line_total"))
        if explicit_line_total is not None:
            line_total = explicit_line_total

        computed_unit_price = coerce_float(entry.get("price"), default=0.0) or 0.0
        if abs(quantity) < 1e-9:
            computed_unit_price = float(line_total)
        if abs(quantity) > 1e-9 and abs(computed_unit_price) < 1e-9:
            computed_unit_price = float(line_total) / float(quantity)

        row_record = PosSalesImportRow(
            sales_import=pos_import,
            import_location=location_record,
            source_product_name=product_name,
            source_product_code=entry.get("source_product_code"),
            normalized_product_name=normalized_product,
            product_id=product_id,
            quantity=quantity,
            net_inc=net_inc,
            discount_raw=None if discount_raw is None else str(discount_raw),
            discount_abs=abs(discount_value),
            computed_line_total=line_total,
            computed_unit_price=computed_unit_price,
            parse_index=row_index_by_location[location_name],
            is_zero_quantity=abs(quantity) < 1e-9,
        )
        db.session.add(row_record)
        row_index_by_location[location_name] += 1


def ingest_pos_sales_attachment(
    *,
    source_provider: str,
    source_message_id: str,
    filename: str,
    content: bytes,
    storage_dir: str | Path,
) -> tuple[PosSalesImport, bool]:
    """Persist and stage a single POS sales attachment.

    Returns ``(sales_import, duplicate)`` where ``duplicate`` indicates an existing
    idempotent import record was reused.
    """

    extension = Path(filename).suffix.lower()
    if not extension:
        raise ValueError("Attachment is missing a file extension.")

    attachment_sha256 = hashlib.sha256(content).hexdigest()
    destination_dir = Path(storage_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)
    persisted_path = destination_dir / f"{attachment_sha256}{extension}"
    if not persisted_path.exists():
        persisted_path.write_bytes(content)

    received_at = datetime.utcnow()
    sales_import = PosSalesImport(
        source_provider=source_provider,
        message_id=source_message_id,
        attachment_filename=filename,
        attachment_sha256=attachment_sha256,
        attachment_storage_path=str(persisted_path),
        sales_date=_default_sales_import_date(received_at),
        received_at=received_at,
        status="pending",
    )
    db.session.add(sales_import)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        existing = PosSalesImport.query.filter_by(
            source_provider=source_provider,
            message_id=source_message_id,
            attachment_sha256=attachment_sha256,
        ).first()
        if existing:
            log_activity(
                f"Skipped duplicate POS sales import for source {source_provider}; existing import {existing.id}"
            )
            return existing, True
        raise

    try:
        stage_pos_sales_import(sales_import, str(persisted_path), extension)
        db.session.commit()
        log_activity(
            f"Received POS sales import {sales_import.id} via {source_provider}"
        )
        return sales_import, False
    except Exception:
        db.session.rollback()
        failure = PosSalesImport(
            source_provider=source_provider,
            message_id=f"{source_message_id}:failed:{secrets.token_hex(4)}",
            attachment_filename=filename,
            attachment_sha256=attachment_sha256,
            attachment_storage_path=str(persisted_path),
            sales_date=_default_sales_import_date(received_at),
            received_at=received_at,
            status="failed",
            failure_reason="Unable to parse POS spreadsheet attachment.",
        )
        db.session.add(failure)
        db.session.commit()
        current_app.logger.exception(
            "Failed to stage inbound POS sales attachment from %s",
            source_provider,
        )
        log_activity(
            "Failed to parse POS sales import attachment via "
            f"{source_provider}; failure import {failure.id}"
        )
        raise
