from __future__ import annotations

from urllib.parse import urlparse

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from flask_wtf.csrf import validate_csrf

from app import db
from app.forms import (
    ConfirmForm,
    DeleteForm,
    PurchaseOrderForm,
    PurchaseOrderMergeForm,
    ReceiveInvoiceForm,
    VendorItemAliasResolutionForm,
    load_purchase_gl_code_choices,
)
from app.models import (
    GLCode,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    PurchaseInvoice,
    PurchaseInvoiceDraft,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderItemArchive,
    Setting,
    Vendor,
)
from app.utils.activity import log_activity
from app.utils.numeric import coerce_float
from app.routes.report_routes import (
    _invoice_gl_code_rows,
    invoice_gl_code_report,
)
from app.utils.forecasting import DemandForecastingHelper
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.text import build_text_match_predicate
from app.utils.text import normalize_request_text_filter
from app.services.purchase_merge import (
    PurchaseMergeError,
    merge_purchase_orders,
)
from app.utils.filter_state import (
    filters_to_query_args,
    get_filter_defaults,
    normalize_filters,
)
from app.services.purchase_imports import (
    CSVImportError,
    find_preferred_vendor_alias,
    parse_purchase_order_csv,
    preferred_vendor_aliases_for_items,
    resolve_vendor_purchase_lines,
    serialize_parsed_line,
    update_or_create_vendor_alias,
)
from app.services.notification_service import notify_users_for_event

import datetime
import json
import re

from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from wtforms.validators import ValidationError

purchase = Blueprint("purchase", __name__)


def _notify_purchase_order_activity(
    po: PurchaseOrder,
    *,
    event_key: str,
    action: str,
    detail: str | None = None,
    sms_body: str | None = None,
) -> None:
    vendor_name = po.vendor_name or "Unknown vendor"
    body = (
        f"Purchase order #{po.id} for {vendor_name} "
        f"was {action} by {current_user.email}."
    )
    if detail:
        body = f"{body} {detail}"
    notify_users_for_event(
        event_key=event_key,
        subject=f"Purchase order {action}: #{po.id}",
        body=body,
        sms_body=sms_body or f"PO {action}: #{po.id} {vendor_name}",
        exclude_user_ids={current_user.id},
    )


_PURCHASE_UPLOAD_SESSION_KEY = "purchase_order_upload"


def _normalize_purchase_order_filter_status(value: str | None) -> str:
    normalized = (value or "open").strip().lower()
    legacy_map = {
        "pending": "open",
        "completed": "received",
    }
    normalized = legacy_map.get(normalized, normalized)
    if normalized not in {"open", "requested", "ordered", "received", "all"}:
        return "open"
    return normalized


def _purchase_redirect_target(next_url: str | None, fallback_endpoint: str):
    candidate = (next_url or "").strip().replace("\\", "")
    if candidate:
        parsed = urlparse(candidate)
        if (
            not parsed.scheme
            and not parsed.netloc
            and candidate.startswith("/")
            and not candidate.startswith("//")
        ):
            return redirect(candidate)
    return redirect(url_for(fallback_endpoint))


def _duplicate_blocker_destination(category: str) -> dict[str, str]:
    if category == "producer_address":
        return {
            "view": "resolve_producer_address",
            "section": "producer-address-section",
        }
    if category == "duplicate_persistence":
        return {
            "view": "duplicate_resolution",
            "section": "duplicate-resolution-section",
        }
    return {
        "view": "staging_cleanup",
        "section": "staging-cleanup-section",
    }


def _blocked_rows_payload(duplicate_blockers: list[dict]) -> list[dict]:
    payload: list[dict] = []
    for blocker in duplicate_blockers:
        destination = _duplicate_blocker_destination(
            str(blocker.get("category") or "").strip()
        )
        payload.append(
            {
                "row_id": blocker.get("row_id") or blocker.get("id"),
                "row_label": blocker.get("row_label"),
                "category": blocker.get("category"),
                "destination": destination,
                "conflict_keys": blocker.get("conflict_keys")
                or blocker.get("key_fields")
                or {},
                "blocks_import": bool(blocker.get("blocks_import", True)),
            }
        )
    return payload


def _blocking_duplicate_blockers(duplicate_blockers: list[dict]) -> list[dict]:
    return [b for b in duplicate_blockers if bool(b.get("blocks_import", True))]


def _non_blocking_duplicate_blockers(duplicate_blockers: list[dict]) -> list[dict]:
    return [b for b in duplicate_blockers if not bool(b.get("blocks_import", True))]


def _get_enabled_import_vendors():
    def _normalize_label(label: str | None) -> str | None:
        if not label:
            return None
        normalized = label.strip()
        if not normalized:
            return None
        return normalized.upper()

    _CORPORATE_SUFFIXES = {
        "INC",
        "INC.",
        "INCORPORATED",
        "LLC",
        "L.L.C.",
        "LTD",
        "LTD.",
        "LIMITED",
        "CO",
        "CO.",
        "COMPANY",
        "CORP",
        "CORP.",
        "CORPORATION",
    }

    def _tokenize_parts(value: str | None) -> list[str]:
        normalized = _normalize_label(value)
        if not normalized:
            return []
        return [part for part in re.split(r"[\s\W]+", normalized) if part]

    def _strip_corporate_suffixes(parts: list[str]) -> list[str]:
        stripped = list(parts)
        while stripped and stripped[-1] in _CORPORATE_SUFFIXES:
            stripped.pop()
        return stripped

    def _label_variants(label: str | None) -> set[str]:
        normalized = _normalize_label(label)
        if not normalized:
            return set()

        variants = {normalized}
        parts = _tokenize_parts(label)
        if parts:
            variants.add(" ".join(parts))
            stripped = _strip_corporate_suffixes(parts)
            if stripped:
                variants.add(" ".join(stripped))

        return variants

    enabled_names = Setting.get_enabled_purchase_import_vendors()
    if not enabled_names:
        enabled_names = Setting.DEFAULT_PURCHASE_IMPORT_VENDORS

    enabled_labels = {
        variant
        for name in enabled_names
        for variant in _label_variants(name)
    }

    def _vendor_labels(vendor: Vendor) -> set[str]:
        labels: set[str] = set()
        first = _normalize_label(vendor.first_name)
        last = _normalize_label(vendor.last_name)
        first_parts = _tokenize_parts(vendor.first_name)
        last_parts = _tokenize_parts(vendor.last_name)

        if first:
            labels.add(first)
        if last:
            labels.add(last)
        if first and last:
            labels.add(f"{first} {last}")
        if last_parts:
            labels.add(" ".join(last_parts))
        if first and last_parts:
            labels.add(f"{first} {last_parts[0]}")

        stripped_full_name = _strip_corporate_suffixes(first_parts + last_parts)
        if stripped_full_name:
            labels.add(" ".join(stripped_full_name))

        return labels

    return [
        vendor
        for vendor in Vendor.query.filter_by(archived=False).all()
        if _vendor_labels(vendor) & enabled_labels
    ]


def _merge_error_response(message: str, wants_json: bool):
    if wants_json:
        return jsonify({"error": message}), 400
    flash(message, "error")
    return redirect(url_for("purchase.view_purchase_orders"))


def _clear_purchase_upload_state():
    session.pop(_PURCHASE_UPLOAD_SESSION_KEY, None)


def _get_purchase_upload_state() -> dict | None:
    state = session.get(_PURCHASE_UPLOAD_SESSION_KEY)
    if not isinstance(state, dict):
        return None
    return state


def _normalized_duplicate_blockers(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    raw_duplicate_blockers = payload.get("duplicate_blockers") or []
    normalized: list[dict] = []
    for idx, blocker in enumerate(raw_duplicate_blockers):
        if not isinstance(blocker, dict):
            continue
        category = (blocker.get("category") or "duplicate_persistence").strip()
        if category not in {
            "producer_address",
            "duplicate_persistence",
            "staging_integrity",
        }:
            category = "staging_integrity"
        destination = _duplicate_blocker_destination(category)
        normalized.append(
            {
                "id": blocker.get("id") or f"blocker-{idx}",
                "row_id": blocker.get("row_id")
                or blocker.get("id")
                or f"blocked-row-{idx}",
                "row_index": blocker.get("row_index"),
                "row_label": blocker.get("row_label") or f"Row {idx + 1}",
                "category": category,
                "key_fields": blocker.get("key_fields") or {},
                "conflict_keys": blocker.get("conflict_keys")
                or blocker.get("key_fields")
                or {},
                "destination": destination,
                "supports_merge": bool(blocker.get("supports_merge")),
                "blocks_import": bool(blocker.get("blocks_import", True)),
            }
        )
    return normalized


def _duplicate_blocker_counts(duplicate_blockers: list[dict]) -> dict[str, int]:
    blocking = _blocking_duplicate_blockers(duplicate_blockers)
    return {
        "producer_address": len(
            [b for b in blocking if b.get("category") == "producer_address"]
        ),
        "duplicate_persistence": len(
            [b for b in blocking if b.get("category") == "duplicate_persistence"]
        ),
        "staging_integrity": len(
            [b for b in blocking if b.get("category") == "staging_integrity"]
        ),
    }


def _persist_duplicate_blockers(payload: dict, blockers: list[dict]) -> bool:
    payload["duplicate_blockers"] = blockers
    session[_PURCHASE_UPLOAD_SESSION_KEY] = payload
    session.modified = True

    persisted = _get_purchase_upload_state() or {}
    persisted_ids = [str(b.get("id") or "") for b in persisted.get("duplicate_blockers") or []]
    expected_ids = [str(b.get("id") or "") for b in blockers]
    return persisted_ids == expected_ids


def _parse_source_ids(raw_source_ids) -> list[int]:
    source_ids: list[int] = []
    if isinstance(raw_source_ids, str):
        tokens = [t.strip() for t in re.split(r"[\s,]+", raw_source_ids) if t.strip()]
    else:
        tokens = raw_source_ids or []

    for token in tokens:
        try:
            parsed = int(token)
        except (TypeError, ValueError):
            raise PurchaseMergeError(f"Invalid purchase order ID: {token}")
        if parsed <= 0:
            raise PurchaseMergeError("Purchase order IDs must be positive numbers.")
        if parsed not in source_ids:
            source_ids.append(parsed)

    if not source_ids:
        raise PurchaseMergeError("Please provide at least one source purchase order ID.")

    return source_ids


def _coerce_bool_flag(raw_value, *, default: bool = False) -> bool:
    if raw_value is None:
        return default
    if isinstance(raw_value, bool):
        return raw_value
    return str(raw_value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _iter_form_item_indexes(form_data) -> list[str]:
    indexes: list[str] = []
    seen_indexes: set[str] = set()
    for key in form_data.keys():
        match = re.match(r"^items-(\d+)-", str(key))
        if not match:
            continue
        index = match.group(1)
        if index in seen_indexes:
            continue
        seen_indexes.add(index)
        indexes.append(index)
    return sorted(indexes, key=int)


def _collect_purchase_order_item_entries(form_data):
    item_entries = []
    has_incomplete_rows = False
    fallback_counter = 0

    for index in _iter_form_item_indexes(form_data):
        item_id = form_data.get(f"items-{index}-item", type=int)
        unit_id = form_data.get(f"items-{index}-unit", type=int)
        vendor_sku = (form_data.get(f"items-{index}-vendor_sku") or "").strip() or None
        vendor_description = (
            form_data.get(f"items-{index}-vendor_description") or ""
        ).strip() or None
        pack_size = (form_data.get(f"items-{index}-pack_size") or "").strip() or None
        quantity = coerce_float(form_data.get(f"items-{index}-quantity"), default=None)
        unit_cost = coerce_float(form_data.get(f"items-{index}-cost"), default=None)
        position = form_data.get(f"items-{index}-position", type=int)
        item_label = (form_data.get(f"items-{index}-item-label") or "").strip()

        has_row_data = bool(
            item_id
            or item_label
            or vendor_sku
            or unit_id
            or quantity is not None
            or unit_cost is not None
        )
        if not has_row_data:
            continue

        if not item_id or quantity is None:
            has_incomplete_rows = True
            continue

        item_entries.append(
            {
                "item_id": item_id,
                "unit_id": unit_id,
                "vendor_sku": vendor_sku,
                "vendor_description": vendor_description,
                "pack_size": pack_size,
                "quantity": quantity,
                "unit_cost": unit_cost,
                "position": position,
                "fallback": fallback_counter,
            }
        )
        fallback_counter += 1

    item_entries.sort(
        key=lambda entry: (
            entry["position"]
            if entry["position"] is not None
            else entry["fallback"],
            entry["fallback"],
        )
    )
    return item_entries, has_incomplete_rows


def _collect_receive_invoice_item_entries(form_data):
    item_entries = []
    has_incomplete_rows = False
    has_missing_vendor_skus = False
    fallback_counter = 0

    for index in _iter_form_item_indexes(form_data):
        item_id = form_data.get(f"items-{index}-item", type=int)
        unit_id = form_data.get(f"items-{index}-unit", type=int)
        vendor_sku = (form_data.get(f"items-{index}-vendor_sku") or "").strip() or None
        vendor_description = (
            form_data.get(f"items-{index}-vendor_description") or ""
        ).strip() or None
        pack_size = (form_data.get(f"items-{index}-pack_size") or "").strip() or None
        quantity = coerce_float(form_data.get(f"items-{index}-quantity"), default=None)
        cost = coerce_float(form_data.get(f"items-{index}-cost"), default=None)
        container_deposit_raw = coerce_float(
            form_data.get(f"items-{index}-container_deposit"),
            default=None,
        )
        position = form_data.get(f"items-{index}-position", type=int)
        gl_code_id = form_data.get(f"items-{index}-gl_code", type=int) or None
        line_location_id = (
            form_data.get(f"items-{index}-location_id", type=int) or None
        )

        has_row_data = bool(
            item_id
            or vendor_sku
            or unit_id
            or quantity is not None
            or cost is not None
            or container_deposit_raw is not None
            or gl_code_id is not None
            or line_location_id is not None
        )
        if not has_row_data:
            continue

        if not item_id or quantity is None or cost is None:
            has_incomplete_rows = True
            continue
        if not vendor_sku:
            has_missing_vendor_skus = True
            continue

        item_entries.append(
            {
                "item_id": item_id,
                "unit_id": unit_id,
                "vendor_sku": vendor_sku,
                "vendor_description": vendor_description,
                "pack_size": pack_size,
                "quantity": quantity,
                "cost": abs(cost),
                "container_deposit": (
                    abs(container_deposit_raw)
                    if container_deposit_raw is not None
                    else 0.0
                ),
                "deposit_provided": container_deposit_raw is not None,
                "position": position,
                "fallback": fallback_counter,
                "gl_code_id": gl_code_id,
                "location_id": line_location_id,
            }
        )
        fallback_counter += 1

    item_entries.sort(
        key=lambda entry: (
            entry["position"]
            if entry["position"] is not None
            else entry["fallback"],
            entry["fallback"],
        )
    )
    return item_entries, has_incomplete_rows, has_missing_vendor_skus


def _sync_vendor_alias_for_purchase_entry(
    *,
    vendor: Vendor | None,
    entry: dict,
    default_cost: float | None,
):
    if vendor is None or not entry.get("item_id"):
        return

    vendor_sku = entry.get("vendor_sku")
    vendor_description = entry.get("vendor_description")
    pack_size = entry.get("pack_size")
    item_unit_id = entry.get("unit_id")

    if not vendor_description or not pack_size:
        preferred_alias = find_preferred_vendor_alias(
            vendor=vendor,
            item_id=entry.get("item_id"),
            item_unit_id=item_unit_id,
        )
        if preferred_alias is not None:
            if not vendor_description:
                vendor_description = preferred_alias.vendor_description
            if not pack_size:
                pack_size = preferred_alias.pack_size

    if not vendor_sku and not vendor_description:
        return

    alias = update_or_create_vendor_alias(
        vendor=vendor,
        item_id=entry["item_id"],
        item_unit_id=item_unit_id,
        vendor_sku=vendor_sku,
        vendor_description=vendor_description,
        pack_size=pack_size,
        default_cost=default_cost,
    )
    db.session.add(alias)


def _apply_preferred_vendor_alias_defaults(form, vendor_id: int | None) -> None:
    if not vendor_id:
        return

    item_ids: list[int] = []
    row_forms: list[tuple[int, object]] = []
    for item_form in form.items:
        if not item_form.item.data:
            continue
        try:
            item_id = int(item_form.item.data)
        except (TypeError, ValueError):
            continue
        item_ids.append(item_id)
        row_forms.append((item_id, item_form))

    alias_map = preferred_vendor_aliases_for_items(
        vendor_id=vendor_id,
        item_ids=item_ids,
    )
    if not alias_map:
        return

    for item_id, item_form in row_forms:
        alias = alias_map.get(item_id)
        if alias is None:
            continue
        if not item_form.vendor_sku.data:
            item_form.vendor_sku.data = alias.vendor_sku
        if not item_form.vendor_description.data:
            item_form.vendor_description.data = alias.vendor_description
        if not item_form.pack_size.data:
            item_form.pack_size.data = alias.pack_size


def _build_purchase_item_lookup(
    selected_item_ids: list[int],
    vendor_id: int | None = None,
) -> dict[int, dict[str, str]]:
    if not selected_item_ids:
        return {}

    normalized_item_ids = sorted(set(selected_item_ids))
    alias_map = preferred_vendor_aliases_for_items(
        vendor_id=vendor_id,
        item_ids=normalized_item_ids,
    )
    return {
        item.id: {
            "name": item.name,
            "gl_code": item.purchase_gl_code.code if item.purchase_gl_code else "",
            "vendor_sku": (
                alias_map[item.id].vendor_sku
                if item.id in alias_map and alias_map[item.id].vendor_sku
                else ""
            ),
        }
        for item in Item.query.options(selectinload(Item.purchase_gl_code))
        .filter(Item.id.in_(normalized_item_ids))
        .all()
    }


def _validate_json_csrf(payload: dict | None) -> None:
    token = request.headers.get("X-CSRFToken")
    if not token and payload:
        token = payload.get("csrf_token")

    if not token:
        raise PurchaseMergeError("Missing CSRF token for merge request.")

    try:
        validate_csrf(token)
    except ValidationError as exc:
        raise PurchaseMergeError(f"CSRF validation failed: {exc}") from exc


def _purchase_gl_code_choices():
    return (
        GLCode.query.filter(
            or_(GLCode.code.like("5%"), GLCode.code.like("6%"))
        )
        .order_by(GLCode.code)
        .all()
    )


def check_negative_invoice_reverse(invoice_obj):
    """Return warnings if reversing the invoice would cause negative inventory."""
    warnings = []
    for inv_item in invoice_obj.items:
        factor = 1
        if inv_item.unit_id:
            unit = db.session.get(ItemUnit, inv_item.unit_id)
            if unit:
                factor = unit.factor
        itm = db.session.get(Item, inv_item.item_id)
        if itm:
            loc_id = inv_item.location_id or invoice_obj.location_id
            record = LocationStandItem.query.filter_by(
                location_id=loc_id,
                item_id=itm.id,
            ).first()
            current = record.expected_count if record else 0
            new_count = current - inv_item.quantity * factor
            if new_count < 0:
                if record and record.location:
                    location_name = record.location.name
                else:
                    fallback_location = db.session.get(Location, loc_id)
                    if fallback_location:
                        location_name = fallback_location.name
                    else:
                        location_name = invoice_obj.location_name
                warnings.append(
                    f"Reversing this invoice will result in negative inventory for {itm.name} at {location_name}"
                )
        else:
            warnings.append(
                f"Cannot reverse invoice because item '{inv_item.item_name}' no longer exists"
            )
    return warnings


@purchase.route("/purchase_orders", methods=["GET"])
@login_required
def view_purchase_orders():
    """Show purchase orders with optional filters."""
    scope = request.endpoint or "purchase.view_purchase_orders"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args, exclude=("page", "per_page", "reset")
    )
    if default_filters and not active_filters:
        return redirect(
            url_for(
                "purchase.view_purchase_orders",
                **filters_to_query_args(default_filters),
            )
        )

    delete_form = DeleteForm()
    merge_form = PurchaseOrderMergeForm()
    mark_ordered_form = DeleteForm()
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    vendor_id = request.args.get("vendor_id", type=int)
    status = _normalize_purchase_order_filter_status(request.args.get("status"))
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")
    raw_item_ids = request.args.getlist("item_id")

    item_ids = []
    for raw_id in raw_item_ids:
        try:
            parsed_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if parsed_id <= 0 or parsed_id in item_ids:
            continue
        item_ids.append(parsed_id)

    selected_items = []
    if item_ids:
        selected_item_records = Item.query.filter(Item.id.in_(item_ids)).all()
        item_lookup = {item.id: item for item in selected_item_records}
        item_ids = [item_id for item_id in item_ids if item_id in item_lookup]
        selected_items = [item_lookup[item_id] for item_id in item_ids]

    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.datetime.strptime(start_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid start date.", "error")
            return redirect(url_for("purchase.view_purchase_orders"))
    if end_date_str:
        try:
            end_date = datetime.datetime.strptime(end_date_str, "%Y-%m-%d").date()
        except ValueError:
            flash("Invalid end date.", "error")
            return redirect(url_for("purchase.view_purchase_orders"))
    if start_date and end_date and start_date > end_date:
        flash("Invalid date range: start cannot be after end.", "error")
        return redirect(url_for("purchase.view_purchase_orders"))

    query = PurchaseOrder.query

    if status == "open":
        query = query.filter_by(received=False)
    elif status == "requested":
        query = query.filter(
            PurchaseOrder.received.is_(False),
            PurchaseOrder.status == PurchaseOrder.STATUS_REQUESTED,
        )
    elif status == "ordered":
        query = query.filter(
            PurchaseOrder.received.is_(False),
            PurchaseOrder.status == PurchaseOrder.STATUS_ORDERED,
        )
    elif status == "received":
        query = query.filter_by(received=True)

    if item_ids:
        query = query.filter(
            PurchaseOrder.items.any(
                PurchaseOrderItem.item_id.in_(item_ids)
            )
        )

    if vendor_id:
        query = query.filter(PurchaseOrder.vendor_id == vendor_id)
    if start_date:
        query = query.filter(PurchaseOrder.order_date >= start_date)
    if end_date:
        query = query.filter(PurchaseOrder.order_date <= end_date)

    query = query.options(
        selectinload(PurchaseOrder.vendor),
        selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.item),
        selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.product),
        selectinload(PurchaseOrder.items).selectinload(PurchaseOrderItem.unit),
    )

    orders = query.order_by(PurchaseOrder.order_date.desc()).paginate(
        page=page, per_page=per_page
    )

    vendors = Vendor.query.filter_by(archived=False).all()
    upload_vendors = _get_enabled_import_vendors()
    filter_items = (
        Item.query.filter_by(archived=False)
        .order_by(Item.name)
        .all()
    )
    active_item_ids = {item.id for item in filter_items}
    extra_item_options = [
        item for item in selected_items if item.id not in active_item_ids
    ]
    selected_vendor = db.session.get(Vendor, vendor_id) if vendor_id else None
    return render_template(
        "purchase_orders/view_purchase_orders.html",
        orders=orders,
        delete_form=delete_form,
        merge_form=merge_form,
        mark_ordered_form=mark_ordered_form,
        vendors=vendors,
        upload_vendors=upload_vendors,
        vendor_id=vendor_id,
        start_date=start_date_str,
        end_date=end_date_str,
        status=status,
        selected_vendor=selected_vendor,
        filter_items=filter_items,
        extra_item_options=extra_item_options,
        selected_item_ids=item_ids,
        selected_items=selected_items,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


@purchase.route("/purchase_orders/merge", methods=["POST"])
@login_required
def merge_purchase_orders_route():
    """Merge unreceived purchase orders into a single target order."""

    wants_json = request.is_json or request.accept_mimetypes.best == "application/json"
    payload = request.get_json(silent=True) if request.is_json else None
    if payload is None and request.is_json:
        payload = {}

    form = PurchaseOrderMergeForm()
    if not request.is_json and not form.validate_on_submit():
        flash("Invalid merge request. Please check the form inputs.", "error")
        return redirect(url_for("purchase.view_purchase_orders"))

    if request.is_json:
        try:
            _validate_json_csrf(payload)
        except PurchaseMergeError as exc:
            return jsonify({"error": str(exc)}), 400
        target_po_id = payload.get("target_po_id")
        raw_source_ids = payload.get("source_po_ids", [])
        require_expected_date_match = _coerce_bool_flag(
            payload.get("require_expected_date_match"),
            default=True,
        )
    else:
        target_po_id = form.target_po_id.data
        raw_source_ids = form.source_po_ids.data or ""
        require_expected_date_match = form.require_expected_date_match.data

    try:
        target_id = int(target_po_id)
    except (TypeError, ValueError):
        return _merge_error_response(
            "Target purchase order ID is required.", wants_json
        )

    if target_id <= 0:
        return _merge_error_response(
            "Target purchase order ID must be a positive number.", wants_json
        )

    try:
        source_ids = _parse_source_ids(raw_source_ids)
    except PurchaseMergeError as exc:
        return _merge_error_response(str(exc), wants_json)

    try:
        merge_purchase_orders(
            target_po_id=target_id,
            source_po_ids=source_ids,
            require_expected_date_match=require_expected_date_match,
        )
    except PurchaseMergeError as exc:
        return _merge_error_response(f"Merge failed: {exc}", wants_json)
    except Exception:
        current_app.logger.exception(
            "Unexpected purchase order merge failure for target=%s sources=%s",
            target_id,
            source_ids,
        )
        return _merge_error_response(
            "An unexpected error occurred while merging purchase orders.", wants_json
        )

    success_message = (
        f"Merged purchase orders {', '.join(map(str, source_ids))} into {target_id}."
    )
    if wants_json:
        return (
            jsonify(
                {
                    "message": success_message,
                    "target_po_id": target_id,
                    "merged_po_ids": source_ids,
                }
            ),
            200,
        )

    flash(success_message, "success")
    return redirect(url_for("purchase.view_purchase_orders"))


@purchase.route("/purchase_orders/upload", methods=["POST"])
@login_required
def upload_purchase_order():
    file = request.files.get("purchase_order_file")
    vendor_id = request.form.get("vendor_id", type=int)
    vendor = db.session.get(Vendor, vendor_id) if vendor_id else None
    enabled_vendor_ids = {vendor.id for vendor in _get_enabled_import_vendors()}

    if vendor is None or vendor.archived or vendor.id not in enabled_vendor_ids:
        flash("Select an enabled vendor before uploading a purchase order file.", "danger")
        return redirect(url_for("purchase.view_purchase_orders"))

    try:
        parsed_order = parse_purchase_order_csv(file, vendor)
        resolved_lines = resolve_vendor_purchase_lines(vendor, parsed_order.items)
    except CSVImportError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("purchase.view_purchase_orders"))

    payload = {
        "vendor_id": vendor.id,
        "vendor_name": f"{vendor.first_name} {vendor.last_name}",
        "source_filename": getattr(file, "filename", None),
        "order_number": parsed_order.order_number,
        "order_date": parsed_order.order_date.isoformat()
        if parsed_order.order_date
        else None,
        "expected_date": parsed_order.expected_date.isoformat()
        if parsed_order.expected_date
        else None,
        "expected_total": parsed_order.expected_total,
        "items": [],
    }

    for idx, resolved in enumerate(resolved_lines):
        payload["items"].append(
            {
                "index": idx,
                **serialize_parsed_line(resolved.parsed_line),
                "item_id": resolved.item_id,
                "unit_id": resolved.unit_id,
                "cost": resolved.cost,
            }
        )

    session[_PURCHASE_UPLOAD_SESSION_KEY] = payload
    session.modified = True

    unresolved_count = len([line for line in payload["items"] if not line.get("item_id")])
    if unresolved_count:
        flash(
            "We need your help matching a few vendor items before creating the purchase order.",
            "warning",
        )
        return redirect(url_for("purchase.resolve_vendor_items"))

    flash("Purchase order parsed successfully. Review the prefilled form to continue.", "success")
    return redirect(url_for("purchase.create_purchase_order"))


@purchase.route("/purchase_orders/resolve_vendor_items", methods=["GET", "POST"])
@login_required
def resolve_vendor_items():
    payload = _get_purchase_upload_state()
    if not payload:
        flash("Upload a purchase order file to start resolving vendor items.", "warning")
        return redirect(url_for("purchase.create_purchase_order"))

    unresolved_lines = [line for line in payload.get("items", []) if not line.get("item_id")]

    raw_duplicate_blockers = payload.get("duplicate_blockers") or []
    duplicate_blockers = _normalized_duplicate_blockers(payload)
    blocking_duplicate_blockers = _blocking_duplicate_blockers(duplicate_blockers)
    non_blocking_duplicate_blockers = _non_blocking_duplicate_blockers(duplicate_blockers)

    if request.method == "POST" and request.form.get("step") == "resolve_duplicate_blocker":
        blocker_id = (request.form.get("blocker_id") or "").strip()
        action = (request.form.get("blocker_action") or "").strip()
        selected = None
        for blocker in raw_duplicate_blockers:
            if str(blocker.get("id") or "") == blocker_id:
                selected = blocker
                break

        if selected is None:
            flash("The selected duplicate key blocker could not be found.", "warning")
            return redirect(url_for("purchase.resolve_vendor_items"))

        if action == "edit_keys":
            flash("Edit the key fields directly in the source row, then continue import.", "info")
            return redirect(url_for("purchase.resolve_vendor_items"))

        if action == "skip_row":
            selected["resolution"] = "skip"
            raw_duplicate_blockers = [
                blocker
                for blocker in raw_duplicate_blockers
                if str(blocker.get("id") or "") != blocker_id
            ]
            if not _persist_duplicate_blockers(payload, raw_duplicate_blockers):
                flash(
                    "Could not persist duplicate blocker decision. Finalize was not run; please try again.",
                    "danger",
                )
                return redirect(url_for("purchase.resolve_vendor_items"))
            flash("Blocked row skipped for this import.", "success")
            return redirect(url_for("purchase.resolve_vendor_items"))

        if action == "merge_overwrite":
            if not selected.get("supports_merge"):
                flash("Merge/overwrite is not supported for this blocked row.", "warning")
                return redirect(url_for("purchase.resolve_vendor_items"))
            selected["resolution"] = "merge_overwrite"
            raw_duplicate_blockers = [
                blocker
                for blocker in raw_duplicate_blockers
                if str(blocker.get("id") or "") != blocker_id
            ]
            if not _persist_duplicate_blockers(payload, raw_duplicate_blockers):
                flash(
                    "Could not persist duplicate blocker decision. Finalize was not run; please try again.",
                    "danger",
                )
                return redirect(url_for("purchase.resolve_vendor_items"))
            flash("Merge/overwrite has been applied for the blocked row.", "success")
            return redirect(url_for("purchase.resolve_vendor_items"))

        flash("Choose a valid action for the selected blocker.", "warning")
        return redirect(url_for("purchase.resolve_vendor_items"))
    if not unresolved_lines:
        return redirect(url_for("purchase.create_purchase_order"))

    vendor = db.session.get(Vendor, payload.get("vendor_id")) if payload.get("vendor_id") else None
    if vendor is None:
        flash("The selected vendor could not be found.", "danger")
        _clear_purchase_upload_state()
        return redirect(url_for("purchase.create_purchase_order"))

    form = VendorItemAliasResolutionForm()
    form.vendor_id.data = vendor.id
    form.order_date.data = payload.get("order_date")
    form.expected_date.data = payload.get("expected_date")
    form.order_number.data = payload.get("order_number")
    form.expected_total_cost.data = payload.get("expected_total")
    form.parsed_payload.data = json.dumps(payload)
    form.unresolved_payload.data = json.dumps(unresolved_lines)

    form.rows.min_entries = len(unresolved_lines)
    while len(form.rows) < len(unresolved_lines):
        form.rows.append_entry()

    items = (
        Item.query.options(selectinload(Item.units))
        .filter_by(archived=False)
        .order_by(Item.name)
        .all()
    )
    item_choices = [(item.id, item.name) for item in items]
    units_map = {item.id: [(unit.id, unit.name) for unit in item.units] for item in items}

    for idx, row_form in enumerate(form.rows):
        parsed = unresolved_lines[idx]
        if request.method == "GET":
            row_form.vendor_sku.data = parsed.get("vendor_sku")
            row_form.vendor_description.data = parsed.get("vendor_description")
            row_form.pack_size.data = parsed.get("pack_size")
            row_form.quantity.data = parsed.get("quantity")
            row_form.unit_cost.data = parsed.get("unit_cost")

        row_form.item_id.choices = item_choices
        selected_item = row_form.item_id.data or None
        row_form.unit_id.choices = [(0, "—")] + units_map.get(selected_item, [])

    if form.validate_on_submit():
        unresolved_targets = [line for line in payload.get("items", []) if not line.get("item_id")]

        for idx, row_form in enumerate(form.rows):
            parsed = unresolved_targets[idx] if idx < len(unresolved_targets) else None
            if parsed is None:
                continue

            unit_id = row_form.unit_id.data or None
            alias = update_or_create_vendor_alias(
                vendor=vendor,
                item_id=row_form.item_id.data,
                item_unit_id=unit_id,
                vendor_sku=parsed.get("vendor_sku"),
                vendor_description=parsed.get("vendor_description"),
                pack_size=parsed.get("pack_size"),
                default_cost=coerce_float(parsed.get("unit_cost")),
            )
            db.session.add(alias)

            parsed["item_id"] = row_form.item_id.data
            parsed["unit_id"] = unit_id

        db.session.commit()
        session[_PURCHASE_UPLOAD_SESSION_KEY] = payload
        session.modified = True

        flash("Vendor item mappings saved. Review the purchase order details next.", "success")
        return redirect(url_for("purchase.create_purchase_order"))

    gl_codes = _purchase_gl_code_choices()
    return render_template(
        "purchase_orders/resolve_vendor_items.html",
        form=form,
        vendor=vendor,
        unresolved_lines=unresolved_lines,
        units_map=units_map,
        source_filename=payload.get("source_filename"),
        gl_codes=gl_codes,
        duplicate_blockers=duplicate_blockers,
        blocking_duplicate_blockers=blocking_duplicate_blockers,
        non_blocking_duplicate_blockers=non_blocking_duplicate_blockers,
        blocker_counts=_duplicate_blocker_counts(
            _normalized_duplicate_blockers(_get_purchase_upload_state())
        ),
    )


@purchase.route("/purchase_orders/create", methods=["GET", "POST"])
@login_required
def create_purchase_order():
    """Create a purchase order."""
    form = PurchaseOrderForm()
    upload_state = _get_purchase_upload_state()

    duplicate_blockers = _normalized_duplicate_blockers(upload_state)
    blocking_duplicate_blockers = _blocking_duplicate_blockers(duplicate_blockers)
    blocker_counts = _duplicate_blocker_counts(duplicate_blockers)

    if request.args.get("reset_upload"):
        _clear_purchase_upload_state()
        return redirect(url_for("purchase.create_purchase_order"))

    if upload_state and any(not line.get("item_id") for line in upload_state.get("items", [])):
        return redirect(url_for("purchase.resolve_vendor_items"))

    if upload_state and any(blocker_counts.values()):
        blocked_rows = _blocked_rows_payload(blocking_duplicate_blockers)
        wants_json = request.accept_mimetypes["application/json"] > request.accept_mimetypes[
            "text/html"
        ]
        if wants_json:
            return (
                jsonify(
                    {
                        "error": "Finalize preflight blocked by staging conflicts.",
                        "blocked_rows": blocked_rows,
                    }
                ),
                409,
            )
        flash(
            "Continue import is blocked until duplicate-key decisions are saved successfully. Go to blocked rows.",
            "danger",
        )
        return redirect(url_for("purchase.resolve_vendor_items") + "#duplicate-blockers-table")

    if request.method == "GET":
        seed = session.pop("po_recommendation_seed", None)
        if seed:
            vendor_id = seed.get("vendor_id")
            if vendor_id and vendor_id in [choice[0] for choice in form.vendor.choices]:
                form.vendor.data = vendor_id
            order_date = seed.get("order_date")
            expected_date = seed.get("expected_date")
            if order_date:
                try:
                    form.order_date.data = datetime.datetime.strptime(
                        order_date, "%Y-%m-%d"
                    ).date()
                except ValueError:
                    form.order_date.data = datetime.date.today()
            if expected_date:
                try:
                    form.expected_date.data = datetime.datetime.strptime(
                        expected_date, "%Y-%m-%d"
                    ).date()
                except ValueError:
                    form.expected_date.data = datetime.date.today() + datetime.timedelta(
                        days=1
                    )

            items = seed.get("items", [])
            form.items.min_entries = max(len(items), 1)
            while len(form.items) < len(items):
                form.items.append_entry()
            for idx, entry in enumerate(items):
                if idx >= len(form.items):
                    break
                form.items[idx].item.data = entry.get("item_id")
                form.items[idx].unit.data = entry.get("unit_id")
                form.items[idx].vendor_sku.data = entry.get("vendor_sku")
                form.items[idx].quantity.data = entry.get("quantity")
                form.items[idx].cost.data = entry.get("cost")
                form.items[idx].position.data = idx

        if upload_state:
            vendor_id = upload_state.get("vendor_id")
            if vendor_id and vendor_id in [choice[0] for choice in form.vendor.choices]:
                form.vendor.data = vendor_id

            if upload_state.get("order_number"):
                form.order_number.data = upload_state.get("order_number")
            if upload_state.get("expected_total") is not None:
                form.expected_total_cost.data = upload_state.get("expected_total")
            if upload_state.get("order_date"):
                try:
                    form.order_date.data = datetime.date.fromisoformat(
                        upload_state.get("order_date")
                    )
                except ValueError:
                    form.order_date.data = datetime.date.today()
            if upload_state.get("expected_date"):
                try:
                    form.expected_date.data = datetime.date.fromisoformat(
                        upload_state.get("expected_date")
                    )
                except ValueError:
                    form.expected_date.data = datetime.date.today() + datetime.timedelta(
                        days=1
                    )

            parsed_items = [
                line for line in upload_state.get("items", []) if line.get("item_id")
            ]
            form.items.min_entries = max(len(parsed_items), form.items.min_entries)
            while len(form.items) < len(parsed_items):
                form.items.append_entry()
            for idx, parsed_item in enumerate(parsed_items):
                if idx >= len(form.items):
                    break
                form.items[idx].item.data = parsed_item.get("item_id")
                form.items[idx].unit.data = parsed_item.get("unit_id")
                form.items[idx].vendor_sku.data = parsed_item.get("vendor_sku")
                form.items[idx].vendor_description.data = parsed_item.get(
                    "vendor_description"
                )
                form.items[idx].pack_size.data = parsed_item.get("pack_size")
                form.items[idx].quantity.data = parsed_item.get("quantity")
                form.items[idx].cost.data = parsed_item.get("cost")
                form.items[idx].position.data = idx

    if request.method == "GET" and form.order_date.data is None:
        form.order_date.data = datetime.date.today()
    if request.method == "GET" and form.expected_date.data is None:
        form.expected_date.data = datetime.date.today() + datetime.timedelta(days=1)
    if request.method == "GET":
        _apply_preferred_vendor_alias_defaults(form, form.vendor.data or None)
    item_entries = []
    has_incomplete_rows = False
    form_submitted = request.method == "POST"
    form_is_valid = form.validate() if form_submitted else False
    if form_submitted:
        item_entries, has_incomplete_rows = _collect_purchase_order_item_entries(
            request.form
        )
        if has_incomplete_rows and not form_is_valid:
            flash(
                "Each populated purchase-order row must include a selected item and quantity.",
                "error",
            )
    if form_submitted and form_is_valid:
        if has_incomplete_rows:
            flash(
                "Each populated purchase-order row must include a selected item and quantity.",
                "error",
            )
        elif not item_entries:
            flash("Add at least one item before saving the purchase order.", "error")
        else:
            vendor_record = db.session.get(Vendor, form.vendor.data)
            vendor_name = (
                f"{vendor_record.first_name} {vendor_record.last_name}"
                if vendor_record
                else ""
            )
            expected_total = (
                float(form.expected_total_cost.data)
                if form.expected_total_cost.data is not None
                else None
            )
            po = PurchaseOrder(
                vendor_id=form.vendor.data,
                user_id=current_user.id,
                vendor_name=vendor_name,
                order_number=form.order_number.data or None,
                order_date=form.order_date.data,
                expected_date=form.expected_date.data,
                expected_total_cost=expected_total,
                delivery_charge=form.delivery_charge.data or 0.0,
                status=PurchaseOrder.STATUS_REQUESTED,
            )
            db.session.add(po)
            db.session.flush()

            for order_index, entry in enumerate(item_entries):
                db.session.add(
                    PurchaseOrderItem(
                        purchase_order_id=po.id,
                        item_id=entry["item_id"],
                        unit_id=entry["unit_id"],
                        vendor_sku=entry["vendor_sku"],
                        quantity=entry["quantity"],
                        unit_cost=entry.get("unit_cost"),
                        position=order_index,
                    )
                )
                _sync_vendor_alias_for_purchase_entry(
                    vendor=vendor_record,
                    entry=entry,
                    default_cost=entry.get("unit_cost"),
                )

            db.session.commit()
            _clear_purchase_upload_state()
            log_activity(f"Created purchase order {po.id}")
            _notify_purchase_order_activity(
                po, event_key="purchase_order_created", action="created"
            )
            flash("Purchase order created successfully!", "success")
            return redirect(url_for("purchase.view_purchase_orders"))

    selected_item_ids = []
    for item_form in form.items:
        if item_form.item.data:
            try:
                selected_item_ids.append(int(item_form.item.data))
            except (TypeError, ValueError):
                continue
    item_lookup = _build_purchase_item_lookup(
        selected_item_ids,
        form.vendor.data or None,
    )

    codes = _purchase_gl_code_choices()
    return render_template(
        "purchase_orders/create_purchase_order.html",
        form=form,
        gl_codes=codes,
        item_lookup=item_lookup,
        upload_state=upload_state,
    )


@purchase.route(
    "/purchase_orders/recommendations", methods=["GET", "POST"]
)
@login_required
def purchase_order_recommendations():
    """Display demand-based purchase order recommendations."""

    params = request.values if request.method == "POST" else request.args
    raw_lookback = coerce_float(params.get("lookback_days"))
    lookback_days = int(raw_lookback) if raw_lookback is not None else 0
    if not lookback_days:
        lookback_days = 30
    location_id = params.get("location_id", type=int)
    item_id = params.get("item_id", type=int)
    attendance_multiplier = coerce_float(params.get("attendance_multiplier")) or 1.0
    weather_multiplier = coerce_float(params.get("weather_multiplier")) or 1.0
    promo_multiplier = coerce_float(params.get("promo_multiplier")) or 1.0
    raw_lead_time = coerce_float(params.get("lead_time_days"))
    lead_time_days = int(raw_lead_time) if raw_lead_time is not None else 0
    if not lead_time_days:
        lead_time_days = 3

    helper = DemandForecastingHelper(
        lookback_days=lookback_days, lead_time_days=lead_time_days
    )
    recommendations = helper.build_recommendations(
        location_ids=[location_id] if location_id else None,
        item_ids=[item_id] if item_id else None,
        attendance_multiplier=attendance_multiplier,
        weather_multiplier=weather_multiplier,
        promo_multiplier=promo_multiplier,
    )

    vendors = Vendor.query.filter_by(archived=False).all()
    locations = Location.query.filter_by(archived=False).all()

    wants_json = (
        request.args.get("format") == "json"
        or request.accept_mimetypes["application/json"]
        > request.accept_mimetypes["text/html"]
    )

    if wants_json:
        payload = {
            "meta": {
                "lookback_days": lookback_days,
                "attendance_multiplier": attendance_multiplier,
                "weather_multiplier": weather_multiplier,
                "promo_multiplier": promo_multiplier,
                "lead_time_days": lead_time_days,
            },
            "data": [
                {
                    "item_id": rec.item.id,
                    "item_name": rec.item.name,
                    "location_id": rec.location.id,
                    "location_name": rec.location.name,
                    "history": {
                        key: round(value, 6)
                        for key, value in rec.history.items()
                        if key != "last_activity_ts"
                    },
                    "base_consumption": round(rec.base_consumption, 6),
                    "adjusted_demand": round(rec.adjusted_demand, 6),
                    "recommended_quantity": round(rec.recommended_quantity, 6),
                    "suggested_delivery_date": rec.suggested_delivery_date.isoformat(),
                    "default_unit_id": rec.default_unit_id,
                }
                for rec in recommendations
            ],
        }
        return jsonify(payload)

    chart_rows = [
        {
            "label": f"{rec.item.name} @ {rec.location.name}",
            "recommended": rec.recommended_quantity,
            "consumption": rec.base_consumption,
            "incoming": rec.history["transfer_in_qty"]
            + rec.history["invoice_qty"]
            + rec.history["open_po_qty"],
        }
        for rec in recommendations
    ]

    if request.method == "POST" and request.form.get("action") == "seed":
        selected_keys = request.form.getlist("selected_lines")
        if not selected_keys:
            flash("No recommendation lines were selected.", "warning")
        else:
            seed_items = []
            override_map = {
                key: coerce_float(request.form.get(f"override-{key}"))
                for key in selected_keys
            }
            rec_map = {
                f"{rec.item.id}:{rec.location.id}": rec for rec in recommendations
            }
            for key in selected_keys:
                rec = rec_map.get(key)
                if not rec:
                    continue
                quantity = override_map.get(key)
                if quantity is None or quantity <= 0:
                    quantity = rec.recommended_quantity
                if quantity <= 0:
                    continue
                seed_items.append(
                    {
                        "item_id": rec.item.id,
                        "unit_id": rec.default_unit_id,
                        "quantity": float(quantity),
                    }
                )

            vendor_id = request.form.get("seed_vendor_id", type=int)
            expected_date = request.form.get("seed_expected_date")
            order_date = request.form.get("seed_order_date") or datetime.date.today().isoformat()

            if seed_items and vendor_id:
                session["po_recommendation_seed"] = {
                    "vendor_id": vendor_id,
                    "expected_date": expected_date
                    or (recommendations[0].suggested_delivery_date.isoformat()
                        if recommendations
                        else datetime.date.today().isoformat()),
                    "order_date": order_date,
                    "items": seed_items,
                }
                session.modified = True
                flash("Purchase order draft populated from recommendations.", "success")
                return redirect(url_for("purchase.create_purchase_order"))
            if not vendor_id:
                flash("Select a vendor before creating a draft purchase order.", "warning")
            if not seed_items:
                flash("No recommendation lines were eligible to push to a draft.", "warning")

    today = datetime.date.today()

    return render_template(
        "purchase_orders/recommendations.html",
        recommendations=recommendations,
        vendors=vendors,
        locations=locations,
        selected_vendor=params.get("seed_vendor_id", type=int),
        selected_location=location_id,
        selected_item=item_id,
        lookback_days=lookback_days,
        attendance_multiplier=attendance_multiplier,
        weather_multiplier=weather_multiplier,
        promo_multiplier=promo_multiplier,
        lead_time_days=lead_time_days,
        chart_rows=chart_rows,
        today=today,
    )


@purchase.route("/purchase_orders/edit/<int:po_id>", methods=["GET", "POST"])
@login_required
def edit_purchase_order(po_id):
    """Modify a pending purchase order."""
    po = db.session.get(PurchaseOrder, po_id)
    if po is None:
        abort(404)
    if po.received:
        flash("Received purchase orders cannot be edited.", "error")
        return redirect(url_for("purchase.view_purchase_orders"))
    form = PurchaseOrderForm()
    mark_ordered_form = DeleteForm()
    if po.vendor_id and po.vendor_id not in {choice[0] for choice in form.vendor.choices}:
        archived_vendor_label = po.vendor_name or f"Archived Vendor #{po.vendor_id}"
        form.vendor.choices.append((po.vendor_id, archived_vendor_label))
    item_entries = []
    has_incomplete_rows = False
    form_submitted = request.method == "POST"
    form_is_valid = form.validate() if form_submitted else False
    if form_submitted:
        item_entries, has_incomplete_rows = _collect_purchase_order_item_entries(
            request.form
        )
        if has_incomplete_rows and not form_is_valid:
            flash(
                "Each populated purchase-order row must include a selected item and quantity.",
                "error",
            )
    if form_submitted and form_is_valid:
        if has_incomplete_rows:
            flash(
                "Each populated purchase-order row must include a selected item and quantity.",
                "error",
            )
        elif not item_entries:
            flash("Add at least one item before saving the purchase order.", "error")
        else:
            existing_unit_costs = {}
            for poi in po.items:
                key = (poi.item_id, poi.unit_id)
                existing_unit_costs.setdefault(key, []).append(poi.unit_cost)

            po.vendor_id = form.vendor.data
            vendor_record = db.session.get(Vendor, form.vendor.data)
            po.vendor_name = (
                f"{vendor_record.first_name} {vendor_record.last_name}"
                if vendor_record
                else po.vendor_name or ""
            )
            po.order_number = form.order_number.data or None
            po.order_date = form.order_date.data
            po.expected_date = form.expected_date.data
            po.expected_total_cost = (
                float(form.expected_total_cost.data)
                if form.expected_total_cost.data is not None
                else None
            )
            po.delivery_charge = form.delivery_charge.data or 0.0

            PurchaseOrderItem.query.filter_by(purchase_order_id=po.id).delete()

            for order_index, entry in enumerate(item_entries):
                unit_cost = entry.get("unit_cost")
                key = (entry["item_id"], entry["unit_id"])
                if unit_cost is None and key in existing_unit_costs and existing_unit_costs[key]:
                    unit_cost = existing_unit_costs[key].pop(0)
                db.session.add(
                    PurchaseOrderItem(
                        purchase_order_id=po.id,
                        item_id=entry["item_id"],
                        unit_id=entry["unit_id"],
                        vendor_sku=entry["vendor_sku"],
                        quantity=entry["quantity"],
                        unit_cost=unit_cost,
                        position=order_index,
                    )
                )
                _sync_vendor_alias_for_purchase_entry(
                    vendor=vendor_record,
                    entry=entry,
                    default_cost=unit_cost,
                )

            db.session.commit()
            log_activity(f"Edited purchase order {po.id}")
            _notify_purchase_order_activity(
                po, event_key="purchase_order_updated", action="updated"
            )
            flash("Purchase order updated successfully!", "success")
            return redirect(url_for("purchase.view_purchase_orders"))

    if request.method == "GET":
        form.vendor.data = po.vendor_id
        form.order_number.data = po.order_number
        form.order_date.data = po.order_date
        form.expected_date.data = po.expected_date
        if po.expected_total_cost is not None:
            form.expected_total_cost.data = po.expected_total_cost
        form.delivery_charge.data = po.delivery_charge
        form.items.min_entries = max(1, len(po.items))
        vendor_record = db.session.get(Vendor, po.vendor_id) if po.vendor_id else None
        for i, poi in enumerate(po.items):
            if len(form.items) <= i:
                form.items.append_entry()
        for i, poi in enumerate(po.items):
            preferred_alias = find_preferred_vendor_alias(
                vendor=vendor_record,
                item_id=poi.item_id,
                item_unit_id=poi.unit_id,
            )
            form.items[i].item.data = poi.item_id
            form.items[i].unit.data = poi.unit_id
            form.items[i].vendor_sku.data = poi.vendor_sku
            form.items[i].vendor_description.data = (
                preferred_alias.vendor_description if preferred_alias else None
            )
            form.items[i].pack_size.data = (
                preferred_alias.pack_size if preferred_alias else None
            )
            form.items[i].quantity.data = poi.quantity
            form.items[i].cost.data = poi.unit_cost
            form.items[i].position.data = poi.position
        _apply_preferred_vendor_alias_defaults(form, po.vendor_id)

    selected_item_ids = []
    for item_form in form.items:
        if item_form.item.data:
            try:
                selected_item_ids.append(int(item_form.item.data))
            except (TypeError, ValueError):
                continue
    item_lookup = _build_purchase_item_lookup(
        selected_item_ids,
        form.vendor.data or po.vendor_id,
    )

    codes = _purchase_gl_code_choices()
    return render_template(
        "purchase_orders/edit_purchase_order.html",
        form=form,
        po=po,
        mark_ordered_form=mark_ordered_form,
        gl_codes=codes,
        item_lookup=item_lookup,
    )


@purchase.route("/purchase_orders/<int:po_id>/mark_ordered", methods=["POST"])
@login_required
def mark_purchase_order_ordered(po_id):
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)

    po = db.session.get(PurchaseOrder, po_id)
    if po is None:
        abort(404)

    next_url = request.form.get("next")
    if po.received:
        flash("Received purchase orders cannot be marked as ordered.", "error")
        return _purchase_redirect_target(next_url, "purchase.view_purchase_orders")

    if po.can_mark_ordered:
        po.status = PurchaseOrder.STATUS_ORDERED
        db.session.add(po)
        db.session.commit()
        log_activity(f"Marked purchase order {po.id} as ordered")
        _notify_purchase_order_activity(
            po,
            event_key="purchase_order_marked_ordered",
            action="marked as ordered",
        )
        flash("Purchase order marked as ordered.", "success")
    else:
        flash("This purchase order is already marked as ordered.", "info")

    return _purchase_redirect_target(next_url, "purchase.view_purchase_orders")


@purchase.route("/purchase_orders/<int:po_id>/delete", methods=["POST"])
@login_required
def delete_purchase_order(po_id):
    """Delete an unreceived purchase order."""
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    po = db.session.get(PurchaseOrder, po_id)
    if po is None:
        abort(404)
    if po.received:
        flash(
            "Cannot delete a purchase order that has been received.", "error"
        )
        return redirect(url_for("purchase.view_purchase_orders"))
    db.session.delete(po)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        flash(
            "Could not delete this purchase order because dependent records still exist.",
            "error",
        )
        return redirect(url_for("purchase.view_purchase_orders"))
    log_activity(f"Deleted purchase order {po.id}")
    flash("Purchase order deleted successfully!", "success")
    return redirect(url_for("purchase.view_purchase_orders"))


@purchase.route(
    "/purchase_orders/<int:po_id>/receive", methods=["GET", "POST"]
)
@login_required
def receive_invoice(po_id):
    """Receive a purchase order and create an invoice."""
    po = db.session.get(PurchaseOrder, po_id)
    if po is None:
        abort(404)
    if po.received:
        flash("This purchase order has already been received.", "error")
        return redirect(url_for("purchase.view_purchase_invoices"))
    form = ReceiveInvoiceForm()
    vendor_record = db.session.get(Vendor, po.vendor_id) if po.vendor_id else None
    gl_code_choices = load_purchase_gl_code_choices()
    department_defaults = Setting.get_receive_location_defaults()
    draft = PurchaseInvoiceDraft.query.filter_by(purchase_order_id=po.id).first()
    draft_data = draft.data if draft else None
    if request.method == "GET":
        po_items_by_position = {poi.position: poi for poi in po.items}
        prefill_items = []
        if draft_data:
            prefill_items = draft_data.get("items", []) or []
        if not prefill_items:
            prefill_items = [
                {
                    "item_id": poi.item_id,
                    "unit_id": poi.unit_id,
                    "vendor_sku": poi.vendor_sku,
                    "quantity": poi.quantity,
                    "position": poi.position,
                    "gl_code_id": None,
                    "cost": poi.unit_cost,
                    "location_id": None,
                }
                for poi in po.items
            ]

        form.items.min_entries = max(1, len(prefill_items))
        while len(form.items) < len(prefill_items):
            form.items.append_entry()

        if draft_data:
            form.invoice_number.data = draft_data.get("invoice_number")
            if draft_data.get("received_date"):
                try:
                    form.received_date.data = datetime.date.fromisoformat(
                        draft_data["received_date"]
                    )
                except ValueError:
                    pass
            if draft_data.get("department"):
                form.department.data = draft_data.get("department")
            if draft_data.get("gst") is not None:
                form.gst.data = draft_data.get("gst")
            if draft_data.get("pst") is not None:
                form.pst.data = draft_data.get("pst")
            if draft_data.get("delivery_charge") is not None:
                form.delivery_charge.data = draft_data.get("delivery_charge")
            invoice_location_id = draft_data.get("location_id")
            if invoice_location_id and any(
                choice_id == invoice_location_id
                for choice_id, _ in form.location_id.choices
            ):
                form.location_id.data = invoice_location_id
        else:
            form.delivery_charge.data = po.delivery_charge
            if not form.received_date.data:
                form.received_date.data = datetime.date.today()

        selected_department = form.department.data or ""
        if not form.location_id.data:
            default_location_id = department_defaults.get(selected_department)
            if default_location_id and any(
                choice_id == default_location_id
                for choice_id, _ in form.location_id.choices
            ):
                form.location_id.data = default_location_id

        location_choices = [(0, "Use Invoice Location")] + [
            (value, label) for value, label in form.location_id.choices
        ]
        for item_form in form.items:
            item_form.item.choices = [
                (i.id, i.name)
                for i in Item.query.filter_by(archived=False).all()
            ]
            item_form.unit.choices = [
                (u.id, u.name) for u in ItemUnit.query.all()
            ]
            item_form.location_id.choices = location_choices
            if item_form.location_id.data is None:
                item_form.location_id.data = 0
            item_form.gl_code.choices = [
                (value, label) for value, label in gl_code_choices
            ]
        for index, item_data in enumerate(prefill_items):
            if index >= len(form.items):
                break
            preferred_alias = find_preferred_vendor_alias(
                vendor=vendor_record,
                item_id=item_data.get("item_id"),
                item_unit_id=item_data.get("unit_id"),
            )
            po_item = po_items_by_position.get(item_data.get("position"))
            form.items[index].item.data = item_data.get("item_id")
            form.items[index].unit.data = item_data.get("unit_id")
            form.items[index].vendor_sku.data = item_data.get("vendor_sku") or (
                po_item.vendor_sku if po_item else None
            )
            form.items[index].vendor_description.data = item_data.get(
                "vendor_description"
            ) or (preferred_alias.vendor_description if preferred_alias else None)
            form.items[index].pack_size.data = item_data.get("pack_size") or (
                preferred_alias.pack_size if preferred_alias else None
            )
            if item_data.get("quantity") is not None:
                form.items[index].quantity.data = item_data.get("quantity")
            if item_data.get("cost") is not None:
                form.items[index].cost.data = item_data.get("cost")
            if item_data.get("container_deposit") is not None:
                form.items[index].container_deposit.data = item_data.get(
                    "container_deposit"
                )
            form.items[index].position.data = item_data.get("position")
            gl_code_value = item_data.get("gl_code_id")
            form.items[index].gl_code.data = gl_code_value or 0
            location_value = item_data.get("location_id")
            form.items[index].location_id.data = location_value or 0
    if form.validate_on_submit():
        (
            item_entries,
            has_incomplete_rows,
            has_missing_vendor_skus,
        ) = _collect_receive_invoice_item_entries(request.form)
        if has_incomplete_rows:
            flash(
                "Each populated invoice row must include an item, quantity, and cost.",
                "error",
            )
            return render_template(
                "purchase_orders/receive_invoice.html",
                form=form,
                po=po,
                gl_code_choices=gl_code_choices,
                department_defaults=department_defaults,
            )
        if has_missing_vendor_skus:
            flash(
                "Each populated invoice row must include a vendor SKU before receiving the invoice.",
                "error",
            )
            return render_template(
                "purchase_orders/receive_invoice.html",
                form=form,
                po=po,
                gl_code_choices=gl_code_choices,
                department_defaults=department_defaults,
            )
        if not item_entries:
            flash("Add at least one item before receiving the invoice.", "error")
            return render_template(
                "purchase_orders/receive_invoice.html",
                form=form,
                po=po,
                gl_code_choices=gl_code_choices,
                department_defaults=department_defaults,
            )

        location_obj = db.session.get(Location, form.location_id.data)
        if not PurchaseOrderItemArchive.query.filter_by(
            purchase_order_id=po.id
        ).first():
            for poi in po.items:
                db.session.add(
                    PurchaseOrderItemArchive(
                        purchase_order_id=po.id,
                        position=poi.position,
                        item_id=poi.item_id,
                        unit_id=poi.unit_id,
                        quantity=poi.quantity,
                        unit_cost=poi.unit_cost,
                    )
                )
        invoice = PurchaseInvoice(
            purchase_order_id=po.id,
            user_id=current_user.id,
            location_id=form.location_id.data,
            vendor_name=po.vendor_name,
            location_name=location_obj.name if location_obj else "",
            received_date=form.received_date.data,
            invoice_number=form.invoice_number.data,
            department=form.department.data or None,
            gst=form.gst.data or 0.0,
            pst=form.pst.data or 0.0,
            delivery_charge=form.delivery_charge.data or 0.0,
        )
        db.session.add(invoice)
        # Flush so the invoice has an ID for related line items without
        # committing the transaction yet. This keeps all updates in a single
        # commit so item cost changes persist reliably.
        db.session.flush()
        starting_item_costs = {}
        starting_item_quantities = {}
        aggregated_inventory_updates = {}
        for entry in item_entries:
            item_id = entry["item_id"]
            if item_id in starting_item_costs:
                continue
            item_obj = db.session.get(Item, item_id)
            starting_item_costs[item_id] = (
                item_obj.cost if item_obj and item_obj.cost else 0.0
            )
            starting_item_quantities[item_id] = (
                db.session.query(db.func.sum(LocationStandItem.expected_count))
                .filter(LocationStandItem.item_id == item_id)
                .scalar()
                or 0.0
            )

        for order_index, entry in enumerate(item_entries):
            item_obj = db.session.get(Item, entry["item_id"])
            unit_obj = (
                db.session.get(ItemUnit, entry["unit_id"]) if entry["unit_id"] else None
            )

            prev_cost = starting_item_costs.get(entry["item_id"], 0.0)
            quantity = entry["quantity"]
            cost = entry["cost"]
            container_deposit = entry.get("container_deposit", 0.0)
            factor = unit_obj.factor if unit_obj and unit_obj.factor else 1
            new_qty = quantity * factor
            cost_per_unit = cost / factor if factor else cost

            if item_obj:
                inventory_update = aggregated_inventory_updates.setdefault(
                    item_obj.id,
                    {
                        "quantity": 0.0,
                        "cost_total": 0.0,
                        "last_cost_per_unit": cost_per_unit,
                    },
                )
                inventory_update["quantity"] += new_qty
                inventory_update["cost_total"] += cost_per_unit * new_qty
                inventory_update["last_cost_per_unit"] = cost_per_unit

            db.session.add(
                PurchaseInvoiceItem(
                    invoice_id=invoice.id,
                    item_id=item_obj.id if item_obj else None,
                    unit_id=unit_obj.id if unit_obj else None,
                    item_name=item_obj.name if item_obj else "",
                    unit_name=unit_obj.name if unit_obj else None,
                    vendor_sku=entry["vendor_sku"],
                    quantity=quantity,
                    cost=cost,
                    container_deposit=container_deposit,
                    prev_cost=prev_cost,
                    position=order_index,
                    purchase_gl_code_id=entry["gl_code_id"],
                    location_id=entry["location_id"],
                )
            )
            _sync_vendor_alias_for_purchase_entry(
                vendor=vendor_record,
                entry=entry,
                default_cost=cost,
            )

            if item_obj:
                line_location_id = entry["location_id"] or invoice.location_id
                record = LocationStandItem.query.filter_by(
                    location_id=line_location_id, item_id=item_obj.id
                ).first()
                if not record:
                    record = LocationStandItem(
                        location_id=line_location_id,
                        item_id=item_obj.id,
                        expected_count=0,
                        purchase_gl_code_id=item_obj.purchase_gl_code_id,
                    )
                    db.session.add(record)
                elif (
                    record.purchase_gl_code_id is None
                    and item_obj.purchase_gl_code_id is not None
                ):
                    record.purchase_gl_code_id = item_obj.purchase_gl_code_id
                record.expected_count += quantity * factor

                if entry.get("deposit_provided"):
                    base_deposit = (
                        container_deposit / factor if factor else container_deposit
                    )
                    item_obj.container_deposit = base_deposit
                    db.session.add(item_obj)

        for item_id, inventory_update in aggregated_inventory_updates.items():
            item_obj = db.session.get(Item, item_id)
            if not item_obj:
                continue
            prev_qty = starting_item_quantities.get(item_id, 0.0)
            prev_cost = starting_item_costs.get(item_id, 0.0)
            total_qty = prev_qty + inventory_update["quantity"]
            prev_total_cost = prev_qty * prev_cost
            if total_qty > 0:
                weighted_cost = (
                    prev_total_cost + inventory_update["cost_total"]
                ) / total_qty
            else:
                weighted_cost = inventory_update["last_cost_per_unit"]

            item_obj.quantity = total_qty
            item_obj.cost = weighted_cost
            db.session.add(item_obj)
        po.received = True
        po.status = PurchaseOrder.STATUS_RECEIVED
        db.session.add(po)
        if draft:
            db.session.delete(draft)
        # Commit once so that invoice, items, and updated item costs are saved
        # atomically, ensuring the weighted cost persists in the database.
        db.session.commit()
        log_activity(f"Received invoice {invoice.id} for PO {po.id}")
        _notify_purchase_order_activity(
            po,
            event_key="purchase_order_received",
            action="received",
            detail=(
                f"Invoice #{invoice.id} was received into "
                f"{invoice.location_name or 'the selected location'}."
            ),
            sms_body=f"PO received: #{po.id} {po.vendor_name}",
        )
        flash("Invoice received successfully!", "success")
        return redirect(url_for("purchase.view_purchase_invoices"))

    return render_template(
        "purchase_orders/receive_invoice.html",
        form=form,
        po=po,
        gl_code_choices=gl_code_choices,
        department_defaults=department_defaults,
    )


@purchase.route("/purchase_invoices", methods=["GET"])
@login_required
def view_purchase_invoices():
    """List all received purchase invoices."""
    scope = request.endpoint or "purchase.view_purchase_invoices"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args, exclude=("page", "per_page", "reset")
    )
    if default_filters and not active_filters:
        return redirect(
            url_for(
                "purchase.view_purchase_invoices",
                **filters_to_query_args(default_filters),
            )
        )

    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    invoice_number = (
        normalize_request_text_filter(request.args.get("invoice_number")) or None
    )
    po_number = request.args.get("po_number", type=int)
    vendor_id = request.args.get("vendor_id", type=int)
    location_id = request.args.get("location_id", type=int)
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")
    amount_filter_raw = request.args.get("amount_filter")
    amount_value_raw = request.args.get("amount_value")

    allowed_amount_filters = {"gt", "lt", "eq"}
    amount_filter = (
        amount_filter_raw if amount_filter_raw in allowed_amount_filters else None
    )
    amount_value = (
        coerce_float(amount_value_raw, default=None)
        if amount_value_raw not in (None, "")
        else None
    )

    start_date = None
    end_date = None
    if start_date_str:
        try:
            start_date = datetime.date.fromisoformat(start_date_str)
        except ValueError:
            flash("Invalid start date.", "error")
            return redirect(url_for("purchase.view_purchase_invoices"))
    if end_date_str:
        try:
            end_date = datetime.date.fromisoformat(end_date_str)
        except ValueError:
            flash("Invalid end date.", "error")
            return redirect(url_for("purchase.view_purchase_invoices"))
    if start_date and end_date and start_date > end_date:
        flash("Invalid date range: start cannot be after end.", "error")
        return redirect(url_for("purchase.view_purchase_invoices"))

    raw_item_ids = request.args.getlist("item_id")
    selected_item_ids = []
    seen_item_ids = set()
    for raw_item_id in raw_item_ids:
        try:
            parsed_id = int(raw_item_id)
        except (TypeError, ValueError):
            continue
        if parsed_id in seen_item_ids:
            continue
        seen_item_ids.add(parsed_id)
        selected_item_ids.append(parsed_id)

    items = Item.query.filter_by(archived=False).order_by(Item.name).all()
    selected_item_records = (
        Item.query.filter(Item.id.in_(selected_item_ids)).all()
        if selected_item_ids
        else []
    )
    item_lookup = {item.id: item for item in selected_item_records}
    selected_items = [
        item_lookup[item_id]
        for item_id in selected_item_ids
        if item_id in item_lookup
    ]
    selected_item_ids = [item.id for item in selected_items]
    selected_item_names = [item.name for item in selected_items]
    active_item_ids = {item.id for item in items}
    extra_item_options = [
        item for item in selected_items if item.id not in active_item_ids
    ]

    query = PurchaseInvoice.query.options(
        selectinload(PurchaseInvoice.purchase_order).selectinload(PurchaseOrder.vendor),
        selectinload(PurchaseInvoice.items)
        .selectinload(PurchaseInvoiceItem.item),
        selectinload(PurchaseInvoice.items)
        .selectinload(PurchaseInvoiceItem.unit),
        selectinload(PurchaseInvoice.items)
        .selectinload(PurchaseInvoiceItem.location),
        selectinload(PurchaseInvoice.items)
        .selectinload(PurchaseInvoiceItem.purchase_gl_code),
    )
    if invoice_number:
        query = query.filter(
            build_text_match_predicate(
                PurchaseInvoice.invoice_number, invoice_number, "contains"
            )
        )
    if po_number:
        query = query.filter(PurchaseInvoice.purchase_order_id == po_number)
    if vendor_id:
        query = query.join(PurchaseOrder).filter(PurchaseOrder.vendor_id == vendor_id)
    if location_id:
        query = query.filter(
            or_(
                PurchaseInvoice.location_id == location_id,
                PurchaseInvoice.items.any(
                    PurchaseInvoiceItem.location_id == location_id
                ),
            )
        )
    if start_date:
        query = query.filter(PurchaseInvoice.received_date >= start_date)
    if end_date:
        query = query.filter(PurchaseInvoice.received_date <= end_date)
    if selected_item_ids:
        query = query.filter(
            PurchaseInvoice.items.any(
                PurchaseInvoiceItem.item_id.in_(selected_item_ids)
            )
        )

    if amount_filter and amount_value is not None:
        item_totals_subq = (
            db.session.query(
                PurchaseInvoiceItem.invoice_id.label("invoice_id"),
                func.sum(
                    PurchaseInvoiceItem.quantity
                    * (
                        PurchaseInvoiceItem.cost
                        + PurchaseInvoiceItem.container_deposit
                    )
                ).label("item_sum"),
            )
            .group_by(PurchaseInvoiceItem.invoice_id)
            .subquery()
        )

        query = query.outerjoin(
            item_totals_subq, item_totals_subq.c.invoice_id == PurchaseInvoice.id
        )

        total_expression = (
            func.coalesce(item_totals_subq.c.item_sum, 0)
            + func.coalesce(PurchaseInvoice.delivery_charge, 0)
            + func.coalesce(PurchaseInvoice.gst, 0)
            + func.coalesce(PurchaseInvoice.pst, 0)
        )

        if amount_filter == "gt":
            query = query.filter(total_expression > amount_value)
        elif amount_filter == "lt":
            query = query.filter(total_expression < amount_value)
        elif amount_filter == "eq":
            query = query.filter(total_expression == amount_value)

    invoices = query.order_by(
        PurchaseInvoice.received_date.desc(), PurchaseInvoice.id.desc()
    ).paginate(page=page, per_page=per_page)

    vendors = Vendor.query.order_by(Vendor.first_name, Vendor.last_name).all()
    locations = Location.query.order_by(Location.name).all()
    active_vendor = db.session.get(Vendor, vendor_id) if vendor_id else None
    active_location = db.session.get(Location, location_id) if location_id else None

    return render_template(
        "purchase_invoices/view_purchase_invoices.html",
        invoices=invoices,
        vendors=vendors,
        locations=locations,
        invoice_number=invoice_number,
        po_number=po_number,
        vendor_id=vendor_id,
        location_id=location_id,
        start_date=start_date_str,
        end_date=end_date_str,
        active_vendor=active_vendor,
        active_location=active_location,
        items=items,
        extra_item_options=extra_item_options,
        selected_items=selected_items,
        selected_item_ids=selected_item_ids,
        selected_item_names=selected_item_names,
        amount_filter=amount_filter,
        amount_value=amount_value,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


@purchase.route("/purchase_invoices/<int:invoice_id>")
@login_required
def view_purchase_invoice(invoice_id):
    """Display a purchase invoice."""
    invoice = (
        PurchaseInvoice.query.options(
            selectinload(PurchaseInvoice.items)
            .selectinload(PurchaseInvoiceItem.item),
            selectinload(PurchaseInvoice.items)
            .selectinload(PurchaseInvoiceItem.unit),
            selectinload(PurchaseInvoice.items)
            .selectinload(PurchaseInvoiceItem.location),
            selectinload(PurchaseInvoice.items)
            .selectinload(PurchaseInvoiceItem.purchase_gl_code),
            selectinload(PurchaseInvoice.purchase_order).selectinload(
                PurchaseOrder.vendor
            ),
            selectinload(PurchaseInvoice.location),
        ).get(invoice_id)
    )
    if invoice is None:
        abort(404)
    log_activity(
        f"Opened purchase invoice {invoice.id} for posting/payment review"
    )
    return render_template(
        "purchase_invoices/view_purchase_invoice.html", invoice=invoice
    )


@purchase.route("/purchase_invoices/<int:invoice_id>/report")
@login_required
def legacy_purchase_invoice_report(invoice_id: int):
    """Backwards compatible endpoint for purchase invoice GL reports."""

    invoice = (
        PurchaseInvoice.query.options(
            selectinload(PurchaseInvoice.items)
            .selectinload(PurchaseInvoiceItem.item),
            selectinload(PurchaseInvoice.items)
            .selectinload(PurchaseInvoiceItem.purchase_gl_code),
            selectinload(PurchaseInvoice.purchase_order).selectinload(
                PurchaseOrder.vendor
            ),
            selectinload(PurchaseInvoice.location),
        )
        .filter_by(id=invoice_id)
        .first()
    )

    if invoice is None:
        abort(404)

    rows, totals = _invoice_gl_code_rows(invoice)
    report_data = {row["code"]: row for row in rows}

    return render_template(
        "report_invoice_gl_code.html",
        invoice=invoice,
        rows=rows,
        totals=totals,
        report=report_data,
    )


@purchase.route(
    "/purchase_invoices/<int:invoice_id>/reverse", methods=["GET", "POST"]
)
@login_required
def reverse_purchase_invoice(invoice_id):
    """Undo receipt of a purchase invoice."""
    invoice = db.session.get(PurchaseInvoice, invoice_id)
    if invoice is None:
        abort(404)
    po = db.session.get(PurchaseOrder, invoice.purchase_order_id)
    if po is None:
        abort(404)
    warnings = check_negative_invoice_reverse(invoice)
    form = ConfirmForm()
    if request.method == "GET":
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings or ["Are you sure you want to reverse this invoice?"],
            action_url=url_for(
                "purchase.reverse_purchase_invoice", invoice_id=invoice_id
            ),
            cancel_url=url_for("purchase.view_purchase_invoices"),
            title="Confirm Invoice Reversal",
        )
    if warnings and "submit" not in request.form:
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "purchase.reverse_purchase_invoice", invoice_id=invoice_id
            ),
            cancel_url=url_for("purchase.view_purchase_invoices"),
            title="Confirm Invoice Reversal",
        )
    draft_payload = {
        "invoice_number": invoice.invoice_number,
        "received_date": invoice.received_date.isoformat()
        if invoice.received_date
        else None,
        "location_id": invoice.location_id,
        "department": invoice.department,
        "gst": invoice.gst,
        "pst": invoice.pst,
        "delivery_charge": invoice.delivery_charge,
        "items": [
            {
                "item_id": inv_item.item_id,
                "unit_id": inv_item.unit_id,
                "vendor_sku": inv_item.vendor_sku,
                "quantity": inv_item.quantity,
                "cost": inv_item.cost,
                "container_deposit": inv_item.container_deposit,
                "position": inv_item.position,
                "gl_code_id": inv_item.purchase_gl_code_id,
                "location_id": inv_item.location_id,
            }
            for inv_item in invoice.items
        ],
    }
    existing_draft = PurchaseInvoiceDraft.query.filter_by(
        purchase_order_id=po.id
    ).first()
    if existing_draft:
        existing_draft.update_payload(draft_payload)
    else:
        db.session.add(
            PurchaseInvoiceDraft(
                purchase_order_id=po.id,
                payload=json.dumps(draft_payload),
            )
        )
    for inv_item in invoice.items:
        factor = 1
        if inv_item.unit_id:
            unit = db.session.get(ItemUnit, inv_item.unit_id)
            if unit:
                factor = unit.factor
        itm = db.session.get(Item, inv_item.item_id)
        if not itm:
            flash(
                f"Cannot reverse invoice because item '{inv_item.item_name}' no longer exists.",
                "error",
            )
            return redirect(url_for("purchase.view_purchase_invoices"))

        removed_qty = inv_item.quantity * factor
        qty_before = itm.quantity
        itm.quantity = qty_before - removed_qty
        itm.cost = inv_item.prev_cost or 0.0

        # Update expected count for the location where items were received
        line_location_id = inv_item.location_id or invoice.location_id
        record = LocationStandItem.query.filter_by(
            location_id=line_location_id,
            item_id=itm.id,
        ).first()
        if not record:
            record = LocationStandItem(
                location_id=line_location_id,
                item_id=itm.id,
                expected_count=0,
                purchase_gl_code_id=itm.purchase_gl_code_id,
            )
            db.session.add(record)
        elif (
            record.purchase_gl_code_id is None
            and itm.purchase_gl_code_id is not None
        ):
            record.purchase_gl_code_id = itm.purchase_gl_code_id
        new_count = record.expected_count - removed_qty
        record.expected_count = new_count

    location_ids = {
        inv_item.location_id or invoice.location_id for inv_item in invoice.items
    }
    missing_locations = [
        loc_id
        for loc_id in location_ids
        if loc_id and not db.session.get(Location, loc_id)
    ]
    if missing_locations:
        flash(
            "Cannot reverse invoice because one or more receiving locations no longer exist.",
            "error",
        )
        return redirect(url_for("purchase.view_purchase_invoices"))

    db.session.delete(invoice)
    po.received = False
    po.status = PurchaseOrder.STATUS_ORDERED
    db.session.commit()
    log_activity(f"Reversed invoice {invoice_id} for PO {po.id}")
    _notify_purchase_order_activity(
        po,
        event_key="purchase_order_reversed",
        action="reversed",
        detail=f"Invoice #{invoice_id} was reversed.",
        sms_body=f"PO reversed: #{po.id} {po.vendor_name}",
    )
    flash("Invoice reversed successfully", "success")
    return redirect(url_for("purchase.view_purchase_orders"))
