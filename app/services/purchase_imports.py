import csv
import datetime
from dataclasses import dataclass
from typing import IO, Iterable, List, Optional

from app import db
from werkzeug.datastructures import FileStorage

from app.models import Item, Vendor, VendorItemAlias
from app.utils.pos_import import normalize_pos_alias
from app.utils.numeric import coerce_float


@dataclass
class ParsedPurchaseLine:
    vendor_sku: Optional[str]
    vendor_description: str
    pack_size: Optional[str]
    quantity: float
    unit_cost: Optional[float] = None


@dataclass
class ResolvedPurchaseLine:
    parsed_line: ParsedPurchaseLine
    alias: Optional[VendorItemAlias]
    item_id: Optional[int] = None
    unit_id: Optional[int] = None
    cost: Optional[float] = None


@dataclass
class ParsedPurchaseOrder:
    items: List[ParsedPurchaseLine]
    order_date: Optional[datetime.date] = None
    expected_date: Optional[datetime.date] = None
    order_number: Optional[str] = None
    expected_total: Optional[float] = None


class CSVImportError(Exception):
    """Raised when a CSV import cannot be processed."""


_SYSCO_REQUIRED_HEADERS = {
    "item": {"item"},
    "description": {"description", "item description"},
    "quantity": {"qty ship", "qty shipped", "quantity shipped"},
    "price": {"price", "unit price"},
}

_SYSCO_OPTIONAL_HEADERS = {
    "ext_price": {"ext price", "extended price", "extd price"},
    "order_number": {"order #", "po number", "order number", "po #"},
}

_PRATTS_REQUIRED_HEADERS = {
    "item": {"item"},
    "pack": {"pack"},
    "size": {"size"},
    "brand": {"brand"},
    "description": {"description"},
    "quantity": {"qty ship", "qty shipped", "quantity shipped"},
    "price": {"price", "unit price"},
    "ext_price": {"ext price", "extended price", "extd price"},
}

_PRATTS_OPTIONAL_HEADERS = {
    "order_number": {"order #", "po number", "order number", "po #"},
}

_MANITOBA_LIQUOR_REQUIRED_HEADERS = {
    "item": {"item number"},
    "description": {"product description"},
    "quantity": {"order quantity"},
    "price": {"unit price"},
    "ext_price": {"extended price"},
}

_MANITOBA_LIQUOR_OPTIONAL_HEADERS = {
    "pack_size": {
        "package size",
        "vol/case size",
        "case size",
        "vol / case size",
    },
    "order_number": {"order no.", "order no", "order number"},
    "original_order_number": {
        "original order no",
        "original order no.",
        "original order number",
    },
    "invoice_date": {"invoice date"},
}


def _prepare_reader(file_obj: IO) -> csv.DictReader:
    file_obj.seek(0)
    return csv.DictReader(
        (line.decode("utf-8", errors="ignore") if isinstance(line, bytes) else line
         for line in file_obj),
    )


def _normalize_header_name(header: str) -> str:
    return " ".join(header.strip().lower().split())


def _normalize_headers(headers):
    return {_normalize_header_name(header): header for header in headers or []}


def _resolve_headers(header_map: dict, required: dict, optional: dict | None = None):
    resolved = {}
    missing = []

    for name, aliases in required.items():
        normalized_aliases = {_normalize_header_name(alias) for alias in aliases}
        match = next((header_map[alias] for alias in normalized_aliases if alias in header_map), None)
        if match:
            resolved[name] = match
        else:
            missing.append(name)

    if optional:
        for name, aliases in optional.items():
            normalized_aliases = {_normalize_header_name(alias) for alias in aliases}
            for alias in normalized_aliases:
                if alias in header_map:
                    resolved[name] = header_map[alias]
                    break

    return resolved, missing


def _iter_excel_rows(file_obj: IO) -> Iterable[list[object]]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - environment specific
        raise CSVImportError(
            "Excel imports require openpyxl. Install dependencies and try again."
        ) from exc

    file_obj.seek(0)
    try:
        workbook = load_workbook(file_obj, read_only=True, data_only=True)
    except Exception as exc:
        raise CSVImportError(
            "Could not read the Excel workbook. Please upload the standard Manitoba Liquor & Lotteries export."
        ) from exc

    try:
        sheet = workbook.active
        try:
            if sheet.calculate_dimension() == "A1:A1":
                # Manitoba's export reports the used range incorrectly.
                sheet.reset_dimensions()
        except Exception:
            pass

        for row in sheet.iter_rows(values_only=True):
            yield list(row)
    finally:
        workbook.close()


def _row_value(row: list[object], index: int) -> object | None:
    if index < 0 or index >= len(row):
        return None
    return row[index]


def _stringify_cell(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return format(value, "f").rstrip("0").rstrip(".")
    if isinstance(value, int):
        return str(value)
    return str(value).strip() or None


def _coerce_excel_date(value: object | None) -> datetime.date | None:
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, (int, float)):
        try:
            from openpyxl.utils.datetime import from_excel
        except ImportError:
            return None
        try:
            parsed = from_excel(value)
        except Exception:
            return None
        if isinstance(parsed, datetime.datetime):
            return parsed.date()
        if isinstance(parsed, datetime.date):
            return parsed
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return datetime.datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
    return None


def _parse_sysco_csv(file_obj: IO) -> ParsedPurchaseOrder:
    reader = _prepare_reader(file_obj)
    header_map = _normalize_headers(reader.fieldnames)
    header_lookup, missing_headers = _resolve_headers(
        header_map, _SYSCO_REQUIRED_HEADERS, _SYSCO_OPTIONAL_HEADERS
    )
    if missing_headers:
        readable = ", ".join(sorted(missing_headers))
        raise CSVImportError(
            f"Missing required Sysco columns: {readable}. Please upload the standard export file."
        )

    items: List[ParsedPurchaseLine] = []
    order_number = None
    expected_total = 0.0
    has_ext_price = False
    for row in reader:
        raw_description = row.get(header_lookup["description"], "").strip()
        raw_qty = row.get(header_lookup["quantity"], "")
        raw_price = row.get(header_lookup["price"], "")
        vendor_sku = row.get(header_lookup["item"], "").strip()

        quantity = coerce_float(raw_qty)
        if quantity is None or quantity <= 0:
            continue

        if not order_number and "order_number" in header_lookup:
            order_number = row.get(header_lookup["order_number"], "").strip() or None

        unit_cost = coerce_float(raw_price)
        if "ext_price" in header_lookup:
            raw_ext = row.get(header_lookup["ext_price"], "")
            ext_cost = coerce_float(raw_ext)
            if ext_cost is not None:
                expected_total += ext_cost
                has_ext_price = True

        items.append(
            ParsedPurchaseLine(
                vendor_sku=vendor_sku or None,
                vendor_description=raw_description or vendor_sku,
                pack_size=None,
                quantity=quantity,
                unit_cost=unit_cost,
            )
        )

    if not items:
        raise CSVImportError("No purchasable lines found in the CSV file.")

    return ParsedPurchaseOrder(
        items=items,
        order_number=order_number,
        expected_total=expected_total if has_ext_price else None,
    )


def _parse_pratts_csv(file_obj: IO) -> ParsedPurchaseOrder:
    reader = _prepare_reader(file_obj)
    header_map = _normalize_headers(reader.fieldnames)
    header_lookup, missing_headers = _resolve_headers(
        header_map, _PRATTS_REQUIRED_HEADERS, _PRATTS_OPTIONAL_HEADERS
    )
    if missing_headers:
        readable = ", ".join(sorted(missing_headers))
        raise CSVImportError(
            f"Missing required Pratts columns: {readable}. Please upload the standard export file."
        )

    items: List[ParsedPurchaseLine] = []
    order_number = None
    expected_total = 0.0
    for row in reader:
        vendor_sku = row.get(header_lookup["item"], "").strip()
        raw_description = row.get(header_lookup["description"], "").strip()
        raw_qty = row.get(header_lookup["quantity"], "")
        raw_price = row.get(header_lookup["price"], "")
        pack = row.get(header_lookup["pack"], "").strip()
        size = row.get(header_lookup["size"], "").strip()

        quantity = coerce_float(raw_qty)
        if quantity is None or quantity <= 0:
            continue

        if not order_number and "order_number" in header_lookup:
            order_number = row.get(header_lookup["order_number"], "").strip() or None

        pack_size = " ".join(filter(None, [pack, size])) or None
        unit_cost = coerce_float(raw_price)
        raw_ext = row.get(header_lookup["ext_price"], "")
        ext_cost = coerce_float(raw_ext)
        if ext_cost is not None:
            expected_total += ext_cost

        items.append(
            ParsedPurchaseLine(
                vendor_sku=vendor_sku or None,
                vendor_description=raw_description or vendor_sku,
                pack_size=pack_size,
                quantity=quantity,
                unit_cost=unit_cost,
            )
        )

    if not items:
        raise CSVImportError("No purchasable lines found in the CSV file.")

    return ParsedPurchaseOrder(
        items=items,
        order_number=order_number,
        expected_total=expected_total,
    )


def _parse_manitoba_liquor_xlsx(file_obj: IO) -> ParsedPurchaseOrder:
    rows = _iter_excel_rows(file_obj)
    header_row = next(rows, None)
    if header_row is None:
        raise CSVImportError("The workbook is empty.")

    header_map = _normalize_headers(
        [_stringify_cell(cell) or "" for cell in header_row]
    )
    header_lookup, missing_headers = _resolve_headers(
        header_map,
        _MANITOBA_LIQUOR_REQUIRED_HEADERS,
        _MANITOBA_LIQUOR_OPTIONAL_HEADERS,
    )
    if missing_headers:
        readable = ", ".join(sorted(missing_headers))
        raise CSVImportError(
            "Missing required Manitoba Liquor & Lotteries columns: "
            f"{readable}. Please upload the standard export file."
        )

    header_indexes = {
        name: header_row.index(header_name)
        for name, header_name in header_lookup.items()
    }

    items: List[ParsedPurchaseLine] = []
    order_number = None
    order_date = None
    expected_total = 0.0
    has_ext_price = False

    for row in rows:
        description = _stringify_cell(
            _row_value(row, header_indexes["description"])
        )
        if not description:
            continue

        raw_ext = _row_value(row, header_indexes["ext_price"])
        ext_cost = coerce_float(raw_ext)
        if ext_cost is not None:
            expected_total += ext_cost
            has_ext_price = True

        if not order_number:
            order_number = _stringify_cell(
                _row_value(row, header_indexes.get("order_number", -1))
            ) or _stringify_cell(
                _row_value(row, header_indexes.get("original_order_number", -1))
            )

        if order_date is None and "invoice_date" in header_indexes:
            order_date = _coerce_excel_date(
                _row_value(row, header_indexes["invoice_date"])
            )

        if description.lower() == "container deposit":
            continue

        quantity = coerce_float(_row_value(row, header_indexes["quantity"]))
        if quantity is None or quantity <= 0:
            continue

        vendor_sku = _stringify_cell(_row_value(row, header_indexes["item"]))
        unit_cost = coerce_float(_row_value(row, header_indexes["price"]))
        pack_size = _stringify_cell(
            _row_value(row, header_indexes.get("pack_size", -1))
        )

        items.append(
            ParsedPurchaseLine(
                vendor_sku=vendor_sku or None,
                vendor_description=description or vendor_sku or "",
                pack_size=pack_size,
                quantity=quantity,
                unit_cost=unit_cost,
            )
        )

    if not items:
        raise CSVImportError("No purchasable lines found in the workbook.")

    return ParsedPurchaseOrder(
        items=items,
        order_number=order_number,
        order_date=order_date,
        expected_total=expected_total if has_ext_price else None,
    )


def parse_purchase_order_csv(file: FileStorage, vendor: Vendor) -> ParsedPurchaseOrder:
    """Parse a vendor purchase-order upload into a purchase order structure."""

    if not file:
        raise CSVImportError("No file was provided for upload.")

    vendor_name = " ".join(
        filter(None, [vendor.first_name, vendor.last_name])
    ).strip().lower()
    if "sysco" in vendor_name:
        return _parse_sysco_csv(file.stream)
    if "pratt" in vendor_name:
        return _parse_pratts_csv(file.stream)
    if "manitoba liquor" in vendor_name or (
        "manitoba" in vendor_name and "lotter" in vendor_name
    ):
        return _parse_manitoba_liquor_xlsx(file.stream)

    raise CSVImportError("Purchase-order imports are not yet supported for this vendor.")


def _default_unit_for_item(item: Item, preferred_unit_id: int | None = None) -> int | None:
    if preferred_unit_id:
        for unit in item.units:
            if unit.id == preferred_unit_id:
                return preferred_unit_id
    for unit in item.units:
        if unit.receiving_default:
            return unit.id
    return item.units[0].id if item.units else None


def normalize_vendor_alias_text(value: str | None) -> str:
    return normalize_pos_alias(value or "")


def find_preferred_vendor_alias(
    *,
    vendor: Vendor | None,
    item_id: int | None,
    item_unit_id: int | None,
) -> VendorItemAlias | None:
    if vendor is None or not item_id:
        return None

    aliases = VendorItemAlias.query.filter_by(
        vendor_id=vendor.id, item_id=item_id
    ).all()
    if not aliases:
        return None

    def _alias_rank(alias: VendorItemAlias):
        updated_at = alias.updated_at or alias.created_at or datetime.datetime.min
        return (
            1 if item_unit_id and alias.item_unit_id == item_unit_id else 0,
            1 if alias.normalized_description else 0,
            1 if alias.item_unit_id is None else 0,
            updated_at,
            alias.id or 0,
        )

    return max(aliases, key=_alias_rank)


def update_or_create_vendor_alias(
    *,
    vendor: Vendor,
    item_id: int,
    item_unit_id: int | None,
    vendor_sku: str | None,
    vendor_description: str | None,
    pack_size: str | None,
    default_cost: float | None,
) -> VendorItemAlias:
    normalized_description = normalize_vendor_alias_text(vendor_description or vendor_sku)

    alias_by_sku = None
    if vendor_sku:
        alias_by_sku = VendorItemAlias.query.filter_by(
            vendor_id=vendor.id, vendor_sku=vendor_sku
        ).first()
    alias_by_description = None
    if normalized_description:
        alias_by_description = VendorItemAlias.query.filter_by(
            vendor_id=vendor.id, normalized_description=normalized_description
        ).first()

    alias = alias_by_sku
    if alias is not None:
        alias.vendor_sku = vendor_sku or None
        alias.vendor_description = vendor_description or vendor_sku or alias.vendor_description
        if alias_by_description is None or alias_by_description.id == alias.id:
            alias.normalized_description = normalized_description or None
        alias.pack_size = pack_size or None
        alias.item_id = item_id
        alias.item_unit_id = item_unit_id
        alias.default_cost = default_cost
        return alias

    if alias_by_description is not None:
        alias_by_description.vendor_description = (
            vendor_description or vendor_sku or alias_by_description.vendor_description
        )
        alias_by_description.normalized_description = normalized_description or None
        alias_by_description.pack_size = pack_size or None
        alias_by_description.item_id = item_id
        alias_by_description.item_unit_id = item_unit_id
        alias_by_description.default_cost = default_cost

        if (
            vendor_sku
            and alias_by_description.vendor_sku
            and alias_by_description.vendor_sku != vendor_sku
        ):
            alias = VendorItemAlias(vendor_id=vendor.id)
            alias.vendor_sku = vendor_sku
            alias.vendor_description = (
                vendor_description
                or alias_by_description.vendor_description
                or vendor_sku
            )
            alias.normalized_description = None
            alias.pack_size = pack_size or alias_by_description.pack_size
            alias.item_id = item_id
            alias.item_unit_id = item_unit_id
            alias.default_cost = default_cost
            db.session.add(alias)
            return alias

        alias_by_description.vendor_sku = vendor_sku or None
        return alias_by_description

    alias = VendorItemAlias(vendor_id=vendor.id)
    alias.vendor_sku = vendor_sku or None
    alias.vendor_description = vendor_description or vendor_sku
    alias.normalized_description = normalized_description or None
    alias.pack_size = pack_size or None
    alias.item_id = item_id
    alias.item_unit_id = item_unit_id
    alias.default_cost = default_cost

    return alias


def resolve_vendor_purchase_lines(
    vendor: Vendor, parsed_lines: List[ParsedPurchaseLine]
) -> List[ResolvedPurchaseLine]:
    if not parsed_lines:
        return []

    vendor_aliases = VendorItemAlias.query.filter_by(vendor_id=vendor.id).all()
    alias_by_sku = {alias.vendor_sku: alias for alias in vendor_aliases if alias.vendor_sku}
    alias_by_description = {
        alias.normalized_description: alias
        for alias in vendor_aliases
        if alias.normalized_description
    }

    resolved: List[ResolvedPurchaseLine] = []
    for parsed_line in parsed_lines:
        normalized_description = normalize_vendor_alias_text(
            parsed_line.vendor_description
        )
        alias = None
        if parsed_line.vendor_sku:
            alias = alias_by_sku.get(parsed_line.vendor_sku)
        if alias is None and normalized_description:
            alias = alias_by_description.get(normalized_description)

        item_id = None
        unit_id = None
        resolved_cost = parsed_line.unit_cost

        if alias and alias.item:
            item_id = alias.item_id
            unit_id = _default_unit_for_item(alias.item, alias.item_unit_id)
            if resolved_cost is None:
                resolved_cost = alias.default_cost

        resolved.append(
            ResolvedPurchaseLine(
                parsed_line=parsed_line,
                alias=alias,
                item_id=item_id,
                unit_id=unit_id,
                cost=resolved_cost,
            )
        )

    return resolved


def serialize_parsed_line(line: ParsedPurchaseLine) -> dict:
    return {
        "vendor_sku": line.vendor_sku,
        "vendor_description": line.vendor_description,
        "pack_size": line.pack_size,
        "quantity": line.quantity,
        "unit_cost": line.unit_cost,
    }
