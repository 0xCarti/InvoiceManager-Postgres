from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date as date_cls, datetime
from decimal import Decimal, InvalidOperation
from typing import IO

from app import db
from app.models import (
    EquipmentAsset,
    EquipmentCategory,
    EquipmentIntakeBatch,
    EquipmentModel,
    Location,
    User,
    Vendor,
)


class EquipmentImportError(Exception):
    """Raised when an equipment import file cannot be processed."""


@dataclass
class ParsedSnipeItAsset:
    asset_tag: str
    asset_name: str | None
    serial_number: str | None
    status: str
    category_name: str | None
    manufacturer: str | None
    model_name: str | None
    model_number: str | None
    purchase_date: date_cls | None
    purchase_cost: float | None
    vendor_name: str | None
    location_name: str | None
    sublocation: str | None
    assigned_to: str | None
    description: str | None
    purchase_order_reference: str | None
    purchase_invoice_reference: str | None
    warranty_expires_on: date_cls | None


_REQUIRED_HEADERS = {
    "asset_tag": {
        "asset tag",
        "asset tag #",
        "asset tag number",
        "asset tag/id",
        "tag",
    },
}

_OPTIONAL_HEADERS = {
    "asset_name": {"asset name", "name"},
    "serial_number": {"serial", "serial number", "serial #"},
    "status": {"status", "asset status"},
    "category_name": {"category", "category name"},
    "manufacturer": {"manufacturer", "brand"},
    "model_name": {"model", "model name"},
    "model_number": {"model number", "model no", "model #"},
    "purchase_date": {"purchase date", "purchased on", "checkout date"},
    "purchase_cost": {"purchase cost", "cost", "purchase cost (cad)", "purchase cost (usd)"},
    "vendor_name": {"supplier", "vendor", "purchased from"},
    "location_name": {"location", "default location"},
    "sublocation": {"rtd location", "location details", "room", "station"},
    "assigned_to": {"assigned to", "assigned user", "checked out to"},
    "description": {"notes", "asset notes", "description"},
    "purchase_order_reference": {"po", "po number", "purchase order", "purchase order number"},
    "purchase_invoice_reference": {"invoice", "invoice number", "purchase invoice"},
    "warranty_expires_on": {
        "warranty expires",
        "warranty expiration",
        "warranty expiry",
        "warranty expires on",
    },
}


def _prepare_reader(file_obj: IO) -> csv.DictReader:
    file_obj.seek(0)
    return csv.DictReader(
        (
            line.decode("utf-8-sig", errors="ignore")
            if isinstance(line, bytes)
            else line
            for line in file_obj
        )
    )


def _normalize_header_name(header: str) -> str:
    return " ".join((header or "").strip().lower().split())


def _normalize_headers(headers) -> dict[str, str]:
    return {_normalize_header_name(header): header for header in headers or []}


def _resolve_headers(header_map: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    resolved: dict[str, str] = {}
    missing: list[str] = []

    for key, aliases in _REQUIRED_HEADERS.items():
        normalized_aliases = {_normalize_header_name(alias) for alias in aliases}
        match = next(
            (header_map[alias] for alias in normalized_aliases if alias in header_map),
            None,
        )
        if match:
            resolved[key] = match
        else:
            missing.append(key)

    for key, aliases in _OPTIONAL_HEADERS.items():
        normalized_aliases = {_normalize_header_name(alias) for alias in aliases}
        match = next(
            (header_map[alias] for alias in normalized_aliases if alias in header_map),
            None,
        )
        if match:
            resolved[key] = match

    return resolved, missing


def _clean_text(value: object | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _parse_float(value: object | None) -> float | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    normalized = cleaned.replace("$", "").replace(",", "")
    try:
        return float(Decimal(normalized))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: object | None) -> date_cls | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


def _normalize_status(value: str | None) -> str:
    normalized = (value or "").strip().lower()
    if not normalized:
        return EquipmentAsset.STATUS_OPERATIONAL
    if any(token in normalized for token in ("lost", "stolen")):
        return EquipmentAsset.STATUS_LOST
    if "dispose" in normalized:
        return EquipmentAsset.STATUS_DISPOSED
    if any(token in normalized for token in ("retired", "archive")):
        return EquipmentAsset.STATUS_RETIRED
    if any(token in normalized for token in ("broken", "repair", "out of service")):
        return EquipmentAsset.STATUS_OUT_OF_SERVICE
    if any(token in normalized for token in ("service", "maintenance")):
        return EquipmentAsset.STATUS_NEEDS_SERVICE
    return EquipmentAsset.STATUS_OPERATIONAL


def _vendor_lookup_key(vendor_name: str | None) -> str:
    return " ".join((vendor_name or "").strip().casefold().split())


def _build_vendor_lookup() -> dict[str, Vendor]:
    lookup: dict[str, Vendor] = {}
    for vendor in Vendor.query.all():
        label = f"{vendor.first_name} {vendor.last_name}".strip()
        if label:
            lookup[_vendor_lookup_key(label)] = vendor
    return lookup


def _build_user_lookup() -> dict[str, User]:
    lookup: dict[str, User] = {}
    for user in User.query.filter_by(active=True).all():
        lookup[_vendor_lookup_key(user.email)] = user
        if user.display_name:
            lookup[_vendor_lookup_key(user.display_name)] = user
        lookup[_vendor_lookup_key(user.display_label)] = user
    return lookup


def _match_user(user_lookup: dict[str, User], assigned_to: str | None) -> User | None:
    key = _vendor_lookup_key(assigned_to)
    if not key:
        return None
    if key in user_lookup:
        return user_lookup[key]
    if "@" in key:
        return user_lookup.get(key.split("@", 1)[0])
    return None


def parse_snipe_it_csv(file_obj: IO) -> list[ParsedSnipeItAsset]:
    reader = _prepare_reader(file_obj)
    header_map = _normalize_headers(reader.fieldnames)
    header_lookup, missing_headers = _resolve_headers(header_map)
    if missing_headers:
        raise EquipmentImportError(
            "Missing required Snipe-IT columns: %s."
            % ", ".join(sorted(missing_headers))
        )

    assets: list[ParsedSnipeItAsset] = []
    for row in reader:
        asset_tag = _clean_text(row.get(header_lookup["asset_tag"]))
        if not asset_tag:
            continue
        assets.append(
            ParsedSnipeItAsset(
                asset_tag=asset_tag,
                asset_name=_clean_text(row.get(header_lookup.get("asset_name", ""))),
                serial_number=_clean_text(
                    row.get(header_lookup.get("serial_number", ""))
                ),
                status=_normalize_status(
                    _clean_text(row.get(header_lookup.get("status", "")))
                ),
                category_name=_clean_text(
                    row.get(header_lookup.get("category_name", ""))
                ),
                manufacturer=_clean_text(
                    row.get(header_lookup.get("manufacturer", ""))
                ),
                model_name=_clean_text(row.get(header_lookup.get("model_name", ""))),
                model_number=_clean_text(
                    row.get(header_lookup.get("model_number", ""))
                ),
                purchase_date=_parse_date(
                    row.get(header_lookup.get("purchase_date", ""))
                ),
                purchase_cost=_parse_float(
                    row.get(header_lookup.get("purchase_cost", ""))
                ),
                vendor_name=_clean_text(row.get(header_lookup.get("vendor_name", ""))),
                location_name=_clean_text(
                    row.get(header_lookup.get("location_name", ""))
                ),
                sublocation=_clean_text(
                    row.get(header_lookup.get("sublocation", ""))
                ),
                assigned_to=_clean_text(row.get(header_lookup.get("assigned_to", ""))),
                description=_clean_text(
                    row.get(header_lookup.get("description", ""))
                ),
                purchase_order_reference=_clean_text(
                    row.get(header_lookup.get("purchase_order_reference", ""))
                ),
                purchase_invoice_reference=_clean_text(
                    row.get(header_lookup.get("purchase_invoice_reference", ""))
                ),
                warranty_expires_on=_parse_date(
                    row.get(header_lookup.get("warranty_expires_on", ""))
                ),
            )
        )
    return assets


def run_snipe_it_import(
    file_obj: IO,
    *,
    default_category_name: str,
    create_missing_locations: bool,
    update_existing: bool,
    imported_by_id: int | None = None,
    source_filename: str | None = None,
) -> dict[str, object]:
    parsed_assets = parse_snipe_it_csv(file_obj)
    if not parsed_assets:
        raise EquipmentImportError("The uploaded CSV did not contain any asset rows.")

    category_lookup = {
        category.name.casefold(): category for category in EquipmentCategory.query.all()
    }
    model_lookup = {
        (
            model.category_id,
            model.manufacturer.casefold(),
            model.name.casefold(),
            (model.model_number or "").casefold(),
        ): model
        for model in EquipmentModel.query.all()
    }
    location_lookup = {
        location.name.casefold(): location for location in Location.query.all()
    }
    vendor_lookup = _build_vendor_lookup()
    user_lookup = _build_user_lookup()
    existing_assets = {
        asset.asset_tag.casefold(): asset for asset in EquipmentAsset.query.all()
    }

    default_category_key = default_category_name.casefold()
    default_category = category_lookup.get(default_category_key)
    if default_category is None:
        default_category = EquipmentCategory(name=default_category_name)
        db.session.add(default_category)
        db.session.flush()
        category_lookup[default_category_key] = default_category

    created_count = 0
    updated_count = 0
    skipped_count = 0
    created_batch_count = 0
    warnings: list[str] = []
    batch_cache: dict[tuple, EquipmentIntakeBatch] = {}
    seen_upload_tags: set[str] = set()

    for parsed_asset in parsed_assets:
        asset_tag_key = parsed_asset.asset_tag.casefold()
        if asset_tag_key in seen_upload_tags:
            skipped_count += 1
            warnings.append(
                f"Skipped duplicate asset tag {parsed_asset.asset_tag} within the upload."
            )
            continue
        seen_upload_tags.add(asset_tag_key)

        existing_asset = existing_assets.get(asset_tag_key)
        if existing_asset is not None and not update_existing:
            skipped_count += 1
            warnings.append(
                f"Skipped existing asset {parsed_asset.asset_tag}; enable updates to refresh existing records."
            )
            continue

        category_name = parsed_asset.category_name or default_category_name
        category_key = category_name.casefold()
        category = category_lookup.get(category_key)
        if category is None:
            category = EquipmentCategory(name=category_name)
            db.session.add(category)
            db.session.flush()
            category_lookup[category_key] = category

        manufacturer = parsed_asset.manufacturer or "Unknown"
        model_name = parsed_asset.model_name or parsed_asset.asset_name or "Imported Asset"
        model_number = parsed_asset.model_number or None
        model_key = (
            category.id,
            manufacturer.casefold(),
            model_name.casefold(),
            (model_number or "").casefold(),
        )
        equipment_model = model_lookup.get(model_key)
        if equipment_model is None:
            equipment_model = EquipmentModel(
                category_id=category.id,
                manufacturer=manufacturer,
                name=model_name,
                model_number=model_number,
            )
            db.session.add(equipment_model)
            db.session.flush()
            model_lookup[model_key] = equipment_model

        purchase_vendor = vendor_lookup.get(_vendor_lookup_key(parsed_asset.vendor_name))
        assigned_user = _match_user(user_lookup, parsed_asset.assigned_to)
        location = None
        location_name = (parsed_asset.location_name or "").strip()
        if location_name:
            location = location_lookup.get(location_name.casefold())
            if location is None and create_missing_locations:
                location = Location(name=location_name)
                db.session.add(location)
                db.session.flush()
                location_lookup[location_name.casefold()] = location

        batch_key = (
            equipment_model.id,
            _vendor_lookup_key(parsed_asset.vendor_name),
            (parsed_asset.purchase_order_reference or "").casefold(),
            (parsed_asset.purchase_invoice_reference or "").casefold(),
            parsed_asset.purchase_date.isoformat()
            if parsed_asset.purchase_date
            else "",
        )
        batch = batch_cache.get(batch_key)
        if batch is None:
            batch = EquipmentIntakeBatch(
                equipment_model_id=equipment_model.id,
                purchase_vendor_id=purchase_vendor.id if purchase_vendor else None,
                vendor_name=parsed_asset.vendor_name,
                purchase_order_reference=parsed_asset.purchase_order_reference,
                purchase_invoice_reference=parsed_asset.purchase_invoice_reference,
                source_type=EquipmentIntakeBatch.SOURCE_SNIPE_IT,
                status=EquipmentIntakeBatch.STATUS_RECEIVED,
                expected_quantity=1,
                unit_cost=parsed_asset.purchase_cost,
                order_date=parsed_asset.purchase_date,
                expected_received_on=parsed_asset.purchase_date,
                received_on=parsed_asset.purchase_date or date_cls.today(),
                created_by_id=imported_by_id,
                notes=(
                    f"Imported from Snipe-IT CSV"
                    + (f" ({source_filename})" if source_filename else "")
                ),
            )
            db.session.add(batch)
            db.session.flush()
            batch_cache[batch_key] = batch
            created_batch_count += 1
        else:
            batch.expected_quantity = int(batch.expected_quantity or 0) + 1
        if batch.unit_cost != parsed_asset.purchase_cost:
            batch.unit_cost = None

        description = parsed_asset.description
        if existing_asset is None:
            asset = EquipmentAsset(
                equipment_model_id=equipment_model.id,
                intake_batch=batch,
                name=parsed_asset.asset_name,
                asset_tag=parsed_asset.asset_tag,
                serial_number=parsed_asset.serial_number,
                status=parsed_asset.status,
                description=description,
                acquired_on=parsed_asset.purchase_date,
                warranty_expires_on=parsed_asset.warranty_expires_on,
                cost=parsed_asset.purchase_cost,
                purchase_vendor_id=purchase_vendor.id if purchase_vendor else None,
                location_id=location.id if location else None,
                sublocation=parsed_asset.sublocation,
                assigned_user_id=assigned_user.id if assigned_user else None,
            )
            db.session.add(asset)
            existing_assets[asset_tag_key] = asset
            created_count += 1
        else:
            existing_asset.equipment_model_id = equipment_model.id
            existing_asset.name = parsed_asset.asset_name or existing_asset.name
            if parsed_asset.serial_number:
                existing_asset.serial_number = parsed_asset.serial_number
            existing_asset.status = parsed_asset.status
            existing_asset.description = description or existing_asset.description
            existing_asset.acquired_on = (
                parsed_asset.purchase_date or existing_asset.acquired_on
            )
            existing_asset.warranty_expires_on = (
                parsed_asset.warranty_expires_on
                or existing_asset.warranty_expires_on
            )
            existing_asset.cost = (
                parsed_asset.purchase_cost
                if parsed_asset.purchase_cost is not None
                else existing_asset.cost
            )
            if purchase_vendor is not None:
                existing_asset.purchase_vendor_id = purchase_vendor.id
            existing_asset.intake_batch = batch
            if location is not None:
                existing_asset.location_id = location.id
            existing_asset.sublocation = parsed_asset.sublocation or existing_asset.sublocation
            if assigned_user is not None:
                existing_asset.assigned_user_id = assigned_user.id
            updated_count += 1

    db.session.flush()
    for batch in batch_cache.values():
        batch.sync_status()

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "created_batch_count": created_batch_count,
        "warnings": warnings[:20],
        "batch_ids": [batch.id for batch in batch_cache.values()],
    }
