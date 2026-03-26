import csv
import datetime
from dataclasses import dataclass
from typing import IO, List, Optional

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

_CENTRAL_SUPPLY_REQUIRED_HEADERS = {
    "item": {"item", "item #", "item number", "vendor sku", "vendor item #"},
    "description": {"description", "item description"},
    "quantity": {
        "qty ship",
        "qty shipped",
        "quantity shipped",
        "qty",
        "order qty",
        "quantity ordered",
    },
    "price": {"price", "unit price", "unit cost"},
    "ext_price": {
        "ext price",
        "extended price",
        "extd price",
        "extended cost",
        "ext cost",
    },
}

_CENTRAL_SUPPLY_OPTIONAL_HEADERS = {
    "pack": {"pack", "pack size", "pack/size", "pack & size"},
    "size": {"size"},
    "order_number": {
        "order #",
        "po number",
        "order number",
        "po #",
        "po no.",
        "order no.",
    },
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


def _parse_central_supply_csv(file_obj: IO) -> ParsedPurchaseOrder:
    reader = _prepare_reader(file_obj)
    header_map = _normalize_headers(reader.fieldnames)
    header_lookup, missing_headers = _resolve_headers(
        header_map, _CENTRAL_SUPPLY_REQUIRED_HEADERS, _CENTRAL_SUPPLY_OPTIONAL_HEADERS
    )
    if missing_headers:
        readable = ", ".join(sorted(missing_headers))
        raise CSVImportError(
            f"Missing required Central Supply columns: {readable}. Please upload the standard export file."
        )

    items: List[ParsedPurchaseLine] = []
    order_number = None
    expected_total = 0.0
    has_ext_price = False

    for row in reader:
        vendor_sku = row.get(header_lookup["item"], "").strip()
        raw_description = row.get(header_lookup["description"], "").strip()
        raw_qty = row.get(header_lookup["quantity"], "")
        raw_price = row.get(header_lookup["price"], "")

        quantity = coerce_float(raw_qty)
        if quantity is None or quantity <= 0:
            continue

        if not order_number and "order_number" in header_lookup:
            order_number = row.get(header_lookup["order_number"], "").strip() or None

        pack = (
            row.get(header_lookup.get("pack", ""), "").strip()
            if "pack" in header_lookup
            else ""
        )
        size = (
            row.get(header_lookup.get("size", ""), "").strip()
            if "size" in header_lookup
            else ""
        )
        pack_size = " ".join(filter(None, [pack, size])) or None

        unit_cost = coerce_float(raw_price)
        raw_ext = row.get(header_lookup["ext_price"], "")
        ext_cost = coerce_float(raw_ext)
        if ext_cost is not None:
            expected_total += ext_cost
            has_ext_price = True

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
        expected_total=expected_total if has_ext_price else None,
    )


def parse_purchase_order_csv(file: FileStorage, vendor: Vendor) -> ParsedPurchaseOrder:
    """Parse a vendor CSV into a purchase order structure."""

    if not file:
        raise CSVImportError("No file was provided for upload.")

    vendor_name = " ".join(filter(None, [vendor.first_name, vendor.last_name])).strip().lower()
    if "central supply" in vendor_name:
        return _parse_central_supply_csv(file.stream)
    if "sysco" in vendor_name:
        return _parse_sysco_csv(file.stream)
    if "pratt" in vendor_name:
        return _parse_pratts_csv(file.stream)

    raise CSVImportError("CSV imports are not yet supported for this vendor.")


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

    alias = None
    if vendor_sku:
        alias = VendorItemAlias.query.filter_by(
            vendor_id=vendor.id, vendor_sku=vendor_sku
        ).first()
    if alias is None and normalized_description:
        alias = VendorItemAlias.query.filter_by(
            vendor_id=vendor.id, normalized_description=normalized_description
        ).first()
    if alias is None:
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
