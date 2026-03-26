import os
import tempfile
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from secrets import token_urlsafe
from typing import Dict

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import login_required

from app import db
from app.forms import (
    DepartmentSalesForecastForm,
    InventoryVarianceReportForm,
    PurchaseCostForecastForm,
    PurchaseInventorySummaryForm,
    ProductRecipeReportForm,
    ProductSalesReportForm,
    QuickProductForm,
    EventTerminalSalesReportForm,
    ReceivedInvoiceReportForm,
    VendorInvoiceReportForm,
)
from app.models import (
    Customer,
    Event,
    EventLocation,
    GLCode,
    Invoice,
    InvoiceProduct,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    TerminalSale,
    TerminalSaleProductAlias,
    Transfer,
    TransferItem,
    User,
)
from app.utils.forecasting import DemandForecastingHelper
from app.utils.pos_import import parse_department_sales_forecast
from app.utils.text import build_text_match_predicate, normalize_request_text_filter
from app.utils.units import (
    DEFAULT_BASE_UNIT_CONVERSIONS,
    convert_cost_for_reporting,
    convert_quantity_for_reporting,
    get_unit_label,
)
from itsdangerous import BadSignature, URLSafeSerializer
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import selectinload
from werkzeug.utils import secure_filename

report = Blueprint("report", __name__)


_CENT = Decimal("0.01")


def _to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _get_base_unit_conversions():
    conversions = current_app.config.get("BASE_UNIT_CONVERSIONS")
    merged = dict(DEFAULT_BASE_UNIT_CONVERSIONS)
    if conversions:
        merged.update(conversions)
    return merged


def _allocate_amount(total: Decimal, weights: Dict[str, Decimal]):
    """Allocate a currency amount across buckets using proportional rounding."""

    allocations = {key: Decimal("0.00") for key in weights}
    total = _quantize(total)

    if not weights or total == 0:
        return allocations

    total_weight = sum(weights.values())
    if total_weight == 0:
        return allocations

    remainder = total
    fractional_shares = []

    for key, weight in weights.items():
        if weight <= 0:
            fractional_shares.append((key, Decimal("0")))
            continue

        raw_share = (total * weight) / total_weight
        rounded_share = raw_share.quantize(_CENT, rounding=ROUND_DOWN)
        allocations[key] = rounded_share
        remainder -= rounded_share
        fractional_shares.append((key, raw_share - rounded_share))

    cents_remaining = int(
        ((remainder / _CENT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    )
    cents_remaining = max(cents_remaining, 0)
    fractional_shares.sort(key=lambda item: item[1], reverse=True)

    if fractional_shares and cents_remaining:
        for i in range(cents_remaining):
            key, _ = fractional_shares[i % len(fractional_shares)]
            allocations[key] += _CENT

    return allocations


def _coerce_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _compute_vendor_invoice_line_base(invoice: Invoice, item: InvoiceProduct) -> float:
    """Compute line base with resilient fallbacks when product links are missing."""

    if item.product is None:
        current_app.logger.warning(
            "Vendor invoice report encountered null product reference "
            "(invoice_id=%s, invoice_product_id=%s).",
            invoice.id,
            item.id,
        )

    line_subtotal = _coerce_float(item.line_subtotal)
    if line_subtotal is not None:
        return line_subtotal

    quantity = _coerce_float(item.quantity) or 0.0
    unit_price = _coerce_float(item.unit_price)
    if unit_price is not None:
        return quantity * unit_price

    if item.product is None:
        return 0.0

    product_price = _coerce_float(item.product.price)
    if product_price is not None:
        return quantity * product_price

    return 0.0


_DEPARTMENT_SALES_STATE_KEY = "department_sales_forecast_state"
_SKIP_SELECTION_VALUE = "__skip__"
_CREATE_SELECTION_VALUE = "__create__"


def _department_sales_serializer() -> URLSafeSerializer:
    secret_key = current_app.config.get("SECRET_KEY")
    if not secret_key:  # pragma: no cover - configuration guard
        raise RuntimeError("Application secret key is not configured.")
    return URLSafeSerializer(secret_key, salt="department-sales-forecast")


def _collect_department_product_totals(payload: dict) -> dict[str, dict]:
    totals: dict[str, dict] = {}
    for department in payload.get("departments", []):
        department_name = department.get("department_name") or ""
        for row in department.get("rows", []):
            normalized = row.get("normalized_name") or ""
            entry = totals.setdefault(
                normalized,
                {
                    "normalized": normalized,
                    "display_name": row.get("product_name")
                    or normalized
                    or "Unmapped product",
                    "quantity": 0.0,
                    "sample_price": row.get("unit_price"),
                    "departments": set(),
                    "source_name": row.get("product_name") or "",
                },
            )
            entry["departments"].add(department_name)
            entry["quantity"] += float(row.get("quantity") or 0.0)
            if entry["sample_price"] is None and row.get("unit_price") is not None:
                entry["sample_price"] = row.get("unit_price")
            if not entry["source_name"]:
                entry["source_name"] = row.get("product_name") or ""
    return totals


def _auto_resolve_department_products(
    product_totals: dict[str, dict]
) -> dict[str, int]:
    mapping: dict[str, int] = {}
    normalized_values = [key for key in product_totals.keys() if key]
    if normalized_values:
        alias_rows = (
            TerminalSaleProductAlias.query.filter(
                TerminalSaleProductAlias.normalized_name.in_(normalized_values)
            ).all()
        )
        for alias in alias_rows:
            if alias.product_id:
                mapping[alias.normalized_name] = alias.product_id

    pending_names: dict[str, str] = {}
    for normalized, entry in product_totals.items():
        if normalized in mapping:
            continue
        name = (entry.get("display_name") or "").strip().lower()
        if not name:
            continue
        pending_names.setdefault(name, normalized)

    if pending_names:
        products = (
            Product.query.filter(func.lower(Product.name).in_(pending_names.keys()))
            .all()
        )
        for product in products:
            key = (product.name or "").strip().lower()
            normalized = pending_names.get(key)
            if normalized and normalized not in mapping:
                mapping[normalized] = product.id

    return mapping


def _merge_product_mappings(
    product_totals: dict[str, dict],
    auto_map: dict[str, int],
    manual_mappings: dict[str, dict] | None,
) -> dict[str, dict]:
    resolved: dict[str, dict] = {}
    manual_mappings = manual_mappings or {}

    for normalized in product_totals.keys():
        manual_entry = manual_mappings.get(normalized)
        if manual_entry:
            status = manual_entry.get("status")
            if status == "skipped":
                resolved[normalized] = {"status": "skipped", "product_id": None}
            elif manual_entry.get("product_id"):
                resolved[normalized] = {
                    "status": "manual",
                    "product_id": manual_entry["product_id"],
                }
            else:
                resolved[normalized] = {"status": "unmapped", "product_id": None}
            continue

        product_id = auto_map.get(normalized)
        if product_id:
            resolved[normalized] = {"status": "auto", "product_id": product_id}
        else:
            resolved[normalized] = {"status": "unmapped", "product_id": None}

    return resolved


def _create_product_from_quick_form(form: QuickProductForm) -> Product:
    cost_value = form.cost.data if form.cost.data is not None else 0.0
    sales_gl_code_id = form.sales_gl_code.data or None
    if sales_gl_code_id == 0:
        sales_gl_code_id = None

    yield_quantity = form.recipe_yield_quantity.data
    if yield_quantity is None or yield_quantity <= 0:
        yield_quantity = 1

    product_obj = Product(
        name=form.name.data,
        price=form.price.data,
        invoice_sale_price=form.invoice_sale_price.data
        if form.invoice_sale_price.data is not None
        else form.price.data,
        cost=cost_value,
        sales_gl_code_id=sales_gl_code_id,
        recipe_yield_quantity=float(yield_quantity),
        recipe_yield_unit=form.recipe_yield_unit.data or None,
    )
    db.session.add(product_obj)
    db.session.flush()

    for item_form in form.items:
        item_id = item_form.item.data
        quantity = item_form.quantity.data
        if not item_id or quantity in (None, ""):
            continue
        unit_id = item_form.unit.data or None
        db.session.add(
            ProductRecipeItem(
                product_id=product_obj.id,
                item_id=item_id,
                unit_id=unit_id,
                quantity=quantity,
                countable=item_form.countable.data,
            )
        )

    return product_obj


def _calculate_department_usage(
    payload: dict, resolved_map: dict[str, dict], only_mapped: bool
):
    conversions = _get_base_unit_conversions()
    product_ids = {
        entry.get("product_id")
        for entry in resolved_map.values()
        if entry.get("product_id")
    }
    products = []
    if product_ids:
        products = (
            Product.query.options(
                selectinload(Product.recipe_items)
                .selectinload(ProductRecipeItem.item)
                .selectinload(Item.units),
                selectinload(Product.recipe_items).selectinload(
                    ProductRecipeItem.unit
                ),
            )
            .filter(Product.id.in_(product_ids))
            .all()
        )
    product_lookup = {product.id: product for product in products}

    warnings = list(payload.get("warnings") or [])
    warning_set = set(warnings)

    def add_warning(message: str) -> None:
        if message not in warning_set:
            warnings.append(message)
            warning_set.add(message)

    overall_items: dict[int, dict] = {}
    overall_unmapped: set[str] = set()
    overall_skipped: set[str] = set()
    department_reports: list[dict] = []

    def build_usage_row(item: Item, base_quantity: float) -> dict:
        converted_qty, report_unit = convert_quantity_for_reporting(
            base_quantity, item.base_unit, conversions
        )
        cost_each = convert_cost_for_reporting(
            float(item.cost or 0.0), item.base_unit, conversions
        )
        total_cost = converted_qty * cost_each

        units = getattr(item, "units", None) or []
        receiving_unit = next(
            (unit for unit in units if unit.receiving_default), None
        )
        receiving_unit_name = None
        receiving_quantity = None
        if receiving_unit is not None:
            receiving_unit_name = receiving_unit.name or None
            factor = float(receiving_unit.factor or 0.0)
            if factor:
                receiving_quantity = base_quantity / factor

        return {
            "item_id": item.id,
            "item_name": item.name,
            "quantity": converted_qty,
            "unit": get_unit_label(report_unit),
            "cost_each": cost_each,
            "total_cost": total_cost,
            "base_quantity": base_quantity,
            "base_unit": item.base_unit,
            "base_unit_label": get_unit_label(item.base_unit),
            "receiving_unit": receiving_unit_name,
            "receiving_quantity": receiving_quantity,
        }

    for department in payload.get("departments", []):
        department_name = department.get("department_name") or ""
        gl_code = department.get("gl_code")
        dept_items: dict[int, dict] = {}
        dept_total_cost = 0.0
        dept_warnings: list[str] = []
        dept_unmapped: list[str] = []
        dept_skipped: list[str] = []

        for row in department.get("rows", []):
            normalized = row.get("normalized_name") or ""
            product_name = row.get("product_name") or "Unmapped product"
            mapping = resolved_map.get(normalized)
            if mapping is None:
                dept_unmapped.append(product_name)
                overall_unmapped.add(product_name)
                continue

            status = mapping.get("status")
            if status == "skipped":
                dept_skipped.append(product_name)
                overall_skipped.add(product_name)
                continue

            product_id = mapping.get("product_id")
            if not product_id:
                dept_unmapped.append(product_name)
                overall_unmapped.add(product_name)
                continue

            product = product_lookup.get(product_id)
            if product is None:
                message = (
                    f"Mapped product '{product_name}' (ID {product_id}) could not be "
                    "loaded for usage calculations."
                )
                add_warning(message)
                dept_warnings.append(message)
                continue

            if not product.recipe_items:
                message = (
                    f"Product '{product.name}' does not have any recipe items; "
                    f"usage for '{product_name}' was skipped."
                )
                add_warning(message)
                dept_warnings.append(message)
                continue

            quantity = float(row.get("quantity") or 0.0)
            if quantity == 0:
                continue

            for recipe_item in product.recipe_items:
                item = recipe_item.item
                if item is None:
                    continue
                base_quantity = float(recipe_item.quantity or 0.0) * quantity
                if recipe_item.unit:
                    base_quantity *= float(recipe_item.unit.factor or 1.0)
                if base_quantity == 0:
                    continue
                entry = dept_items.setdefault(
                    item.id,
                    {"item": item, "base_quantity": 0.0},
                )
                entry["base_quantity"] += base_quantity

        department_items_output: list[dict] = []
        for entry in dept_items.values():
            item = entry["item"]
            base_quantity = entry["base_quantity"]
            usage_row = build_usage_row(item, base_quantity)
            dept_total_cost += usage_row["total_cost"]
            department_items_output.append(usage_row)
            overall_entry = overall_items.setdefault(
                item.id,
                {"item": item, "base_quantity": 0.0},
            )
            overall_entry["base_quantity"] += base_quantity

        department_items_output.sort(key=lambda row: row["item_name"].lower())
        include_department = bool(department_items_output) or not only_mapped
        if include_department:
            department_reports.append(
                {
                    "department_name": department_name,
                    "gl_code": gl_code,
                    "items": department_items_output,
                    "total_cost": dept_total_cost,
                    "warnings": dept_warnings,
                    "unmapped_products": dept_unmapped,
                    "skipped_products": dept_skipped,
                }
            )

    department_reports.sort(key=lambda entry: entry["department_name"].lower())

    overall_items_output: list[dict] = []
    overall_total_cost = 0.0
    for entry in overall_items.values():
        item = entry["item"]
        base_quantity = entry["base_quantity"]
        usage_row = build_usage_row(item, base_quantity)
        overall_total_cost += usage_row["total_cost"]
        overall_items_output.append(usage_row)

    overall_items_output.sort(key=lambda row: row["item_name"].lower())
    overall_summary = {"items": overall_items_output, "total_cost": overall_total_cost}

    return (
        department_reports,
        overall_summary,
        warnings,
        sorted(overall_unmapped),
        sorted(overall_skipped),
    )

@report.route("/reports/department-sales-forecast", methods=["GET", "POST"])
@login_required
def department_sales_forecast():
    if request.args.get("reset") == "1":
        session.pop(_DEPARTMENT_SALES_STATE_KEY, None)
        session.modified = True
        return redirect(url_for("report.department_sales_forecast"))

    form = DepartmentSalesForecastForm()
    state_token = None
    filename = None
    payload = None
    mapping_errors: list[str] = []
    error_targets: set[str] = set()
    posted_selections: dict[str, str] = {}
    product_entries: list[dict] = []
    pending_entries: list[dict] = []
    resolved_entries: list[dict] = []
    product_search_options: list[dict[str, str]] = []
    report_departments: list[dict] = []
    report_overall = None
    overall_warnings: list[str] = []
    overall_unmapped_products: list[str] = []
    overall_skipped_products: list[str] = []
    only_mapped = False
    quick_product_forms: list[dict] = []

    if request.method == "POST" and request.form.get("state_token"):
        serializer = _department_sales_serializer()
        raw_token = request.form.get("state_token", "")
        try:
            state_data = serializer.loads(raw_token)
        except BadSignature:
            session.pop(_DEPARTMENT_SALES_STATE_KEY, None)
            session.modified = True
            flash(
                "The uploaded sales data could not be verified. Upload the file again.",
                "danger",
            )
            return redirect(url_for("report.department_sales_forecast"))

        token_id = state_data.get("token_id")
        expected_id = session.get(_DEPARTMENT_SALES_STATE_KEY)
        if not token_id or expected_id != token_id:
            session.pop(_DEPARTMENT_SALES_STATE_KEY, None)
            session.modified = True
            flash(
                "The department sales forecast session is no longer valid. Upload the file again.",
                "danger",
            )
            return redirect(url_for("report.department_sales_forecast"))

        payload = state_data.get("payload") or {}
        if not isinstance(payload, dict):
            flash("Unable to continue processing the uploaded data.", "danger")
            return redirect(url_for("report.department_sales_forecast"))

        filename = state_data.get("filename")
        options = payload.setdefault("options", {})
        only_mapped = bool(request.form.get("only_mapped"))
        options["only_mapped"] = only_mapped

        totals = _collect_department_product_totals(payload)
        auto_map = _auto_resolve_department_products(totals)
        manual_mappings = payload.get("manual_mappings") or {}
        updated_manual_mappings = dict(manual_mappings)
        pending_alias_updates: list[tuple[str, str, int]] = []
        pending_creations: list[dict] = []
        creation_step_requested = request.form.get("creation-step") == "1"
        creation_errors = False
        created_products: list[Product] = []

        for key in request.form:
            if not key.startswith("product-key-"):
                continue
            normalized = (request.form.get(key) or "").strip()
            if not normalized:
                continue
            suffix = key[len("product-key-") :]
            selection = request.form.get(f"mapping-{suffix}", "")
            posted_selections[normalized] = selection

            if selection == _SKIP_SELECTION_VALUE:
                updated_manual_mappings[normalized] = {"status": "skipped"}
                continue

            if selection == _CREATE_SELECTION_VALUE:
                entry_totals = totals.get(normalized, {})
                pending_creations.append(
                    {
                        "normalized": normalized,
                        "display_name": entry_totals.get("display_name")
                        or entry_totals.get("source_name")
                        or normalized,
                        "source_name": entry_totals.get("source_name")
                        or entry_totals.get("display_name")
                        or normalized,
                        "quantity": entry_totals.get("quantity"),
                        "sample_price": entry_totals.get("sample_price"),
                        "departments": sorted(entry_totals.get("departments", [])),
                    }
                )
                updated_manual_mappings.pop(normalized, None)
                continue

            if not selection:
                updated_manual_mappings.pop(normalized, None)
                continue

            try:
                product_id = int(selection)
            except (TypeError, ValueError):
                mapping_errors.append(
                    "Select a valid product for "
                    f"{totals.get(normalized, {}).get('display_name', 'the selected product')}.",
                )
                error_targets.add(normalized)
                continue

            product = db.session.get(Product, product_id)
            if product is None:
                mapping_errors.append(
                    f"Product ID {product_id} is no longer available. Choose another product."
                )
                error_targets.add(normalized)
                continue

            source_name = totals.get(normalized, {}).get("source_name") or product.name
            updated_manual_mappings[normalized] = {
                "status": "manual",
                "product_id": product.id,
                "source_name": source_name,
            }
            if not normalized.startswith("__unnamed_"):
                pending_alias_updates.append((normalized, source_name, product.id))

        if pending_creations:
            for index, creation_data in enumerate(pending_creations):
                prefix = f"create-{index}"
                formdata = request.form if creation_step_requested else None
                quick_form = QuickProductForm(formdata=formdata, prefix=prefix)
                if not creation_step_requested:
                    quick_form.name.data = creation_data["display_name"]
                    price_value = creation_data.get("sample_price")
                    if price_value is not None:
                        quick_form.price.data = _to_decimal(price_value)
                    else:
                        quick_form.price.data = Decimal("0.00")
                quick_product_forms.append(
                    {
                        "form": quick_form,
                        "index": index,
                        "normalized": creation_data["normalized"],
                        "display_name": creation_data["display_name"],
                        "quantity": creation_data.get("quantity"),
                        "sample_price": creation_data.get("sample_price"),
                        "departments": creation_data.get("departments", []),
                    }
                )

            if not creation_step_requested:
                mapping_errors.append(
                    "Provide details for the new products before continuing."
                )
            else:
                for entry in quick_product_forms:
                    quick_form = entry["form"]
                    if not quick_form.validate():
                        creation_errors = True
                if creation_errors:
                    mapping_errors.append(
                        "Review the new product details and correct any errors before continuing."
                    )
                else:
                    for creation_data, entry in zip(pending_creations, quick_product_forms):
                        quick_form = entry["form"]
                        product_obj = _create_product_from_quick_form(quick_form)
                        source_name = creation_data.get("source_name") or product_obj.name
                        normalized = creation_data["normalized"]
                        updated_manual_mappings[normalized] = {
                            "status": "manual",
                            "product_id": product_obj.id,
                            "source_name": source_name,
                        }
                        if not normalized.startswith("__unnamed_"):
                            pending_alias_updates.append(
                                (normalized, source_name, product_obj.id)
                            )
                        created_products.append(product_obj)
                        posted_selections[normalized] = str(product_obj.id)

        if mapping_errors or (pending_creations and creation_errors):
            db.session.rollback()
        else:
            payload["manual_mappings"] = updated_manual_mappings
            for normalized, source_name, product_id in pending_alias_updates:
                alias = TerminalSaleProductAlias.query.filter_by(
                    normalized_name=normalized
                ).first()
                if alias is None:
                    alias = TerminalSaleProductAlias(
                        source_name=source_name,
                        normalized_name=normalized,
                        product_id=product_id,
                    )
                    db.session.add(alias)
                else:
                    alias.source_name = source_name
                    alias.product_id = product_id
            if pending_alias_updates or created_products:
                db.session.commit()
                if created_products:
                    quick_product_forms = []
            flash("Product mappings updated.", "success")

    elif request.method == "POST" and form.validate_on_submit():
        upload = form.upload.data
        filename = secure_filename(upload.filename or "department-sales.xlsx")
        extension = os.path.splitext(filename)[1].lower()
        tmp_path = None

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
                tmp_path = tmp.name
                upload.save(tmp_path)
            parsed = parse_department_sales_forecast(tmp_path, extension)
        except RuntimeError as exc:
            reason = str(exc)
            if reason == "legacy_xls_missing":
                flash("Legacy Excel support is unavailable on this server.", "danger")
            elif reason in {"legacy_xls_error", "xlsx_error"}:
                flash("The uploaded Excel file could not be read.", "danger")
            elif reason == "xlsx_missing":
                flash("Excel support libraries are unavailable on this server.", "danger")
            elif reason == "unsupported_extension":
                flash("Upload an IdealPOS .xls or .xlsx export.", "danger")
            else:
                flash(
                    "An unexpected error occurred while reading the uploaded file.",
                    "danger",
                )
            return redirect(url_for("report.department_sales_forecast"))
        finally:
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        payload = asdict(parsed)
        if not payload.get("departments"):
            flash(
                "No department sales records were detected in the uploaded file.",
                "warning",
            )
            payload = None
        else:
            payload["manual_mappings"] = {}
            payload["options"] = {"only_mapped": bool(form.only_mapped_products.data)}
            only_mapped = payload["options"]["only_mapped"]
            token_id = token_urlsafe(16)
            session[_DEPARTMENT_SALES_STATE_KEY] = token_id
            session.modified = True
            serializer = _department_sales_serializer()
            state_token = serializer.dumps(
                {"token_id": token_id, "payload": payload, "filename": filename}
            )

    elif request.method == "POST":
        # Form submission failed validation; errors will be displayed.
        pass

    if payload:
        options = payload.setdefault("options", {})
        only_mapped = bool(options.get("only_mapped"))
        form.only_mapped_products.data = only_mapped

        totals = _collect_department_product_totals(payload)
        auto_map = _auto_resolve_department_products(totals)
        resolved_map = _merge_product_mappings(
            totals, auto_map, payload.get("manual_mappings")
        )

        product_choices = Product.query.order_by(Product.name).all()
        product_lookup = {product.id: product for product in product_choices}
        product_search_options = [
            {
                "id": str(product.id),
                "value": f"{product.name} (ID: {product.id})",
                "label": product.name,
            }
            for product in product_choices
        ]

        manual_mappings = payload.get("manual_mappings") or {}
        sorted_keys = sorted(
            totals.keys(), key=lambda key: totals[key]["display_name"].lower()
        )

        for index, normalized in enumerate(sorted_keys):
            entry_totals = totals[normalized]
            resolved = resolved_map.get(
                normalized, {"status": "unmapped", "product_id": None}
            )
            product_id = resolved.get("product_id")
            status = resolved.get("status", "unmapped")

            if normalized in posted_selections:
                selected_value = posted_selections[normalized]
            elif status == "skipped":
                selected_value = _SKIP_SELECTION_VALUE
            elif product_id:
                selected_value = str(product_id)
            else:
                selected_value = ""

            selected_display = ""
            effective_product_id = None
            if selected_value == _SKIP_SELECTION_VALUE:
                status = "skipped"
            elif selected_value:
                try:
                    effective_product_id = int(selected_value)
                except (TypeError, ValueError):
                    effective_product_id = None
                else:
                    product_obj = product_lookup.get(effective_product_id)
                    if product_obj:
                        selected_display = (
                            f"{product_obj.name} (ID: {product_obj.id})"
                        )
                if normalized in manual_mappings:
                    status = manual_mappings.get(normalized, {}).get("status", "manual")
                elif status not in {"auto", "manual"}:
                    status = "manual"
            elif product_id:
                effective_product_id = product_id
                product_obj = product_lookup.get(product_id)
                if product_obj:
                    selected_display = f"{product_obj.name} (ID: {product_obj.id})"

            product_entries.append(
                {
                    "field": f"mapping-{index}",
                    "key_field": f"product-key-{index}",
                    "normalized_name": normalized,
                    "name": entry_totals["display_name"],
                    "departments": sorted(entry_totals["departments"]),
                    "quantity": entry_totals["quantity"],
                    "sample_price": entry_totals.get("sample_price"),
                    "selected": selected_value,
                    "selected_display": selected_display,
                    "status": status,
                    "product_id": effective_product_id or product_id,
                    "has_error": normalized in error_targets,
                }
            )

        pending_entries = [
            entry
            for entry in product_entries
            if not entry.get("product_id") and entry.get("status") != "skipped"
        ]

        resolved_entries = [
            {
                "name": entry["name"],
                "status": entry["status"],
                "product": product_lookup.get(entry["product_id"])
                if entry["product_id"]
                else None,
            }
            for entry in product_entries
            if entry["status"] in {"auto", "manual"} and entry["product_id"]
        ]

        (
            report_departments,
            report_overall,
            overall_warnings,
            overall_unmapped_products,
            overall_skipped_products,
        ) = _calculate_department_usage(payload, resolved_map, only_mapped)

        token_id = session.get(_DEPARTMENT_SALES_STATE_KEY)
        if not token_id:
            token_id = token_urlsafe(16)
            session[_DEPARTMENT_SALES_STATE_KEY] = token_id
            session.modified = True
        serializer = _department_sales_serializer()
        state_token = serializer.dumps(
            {"token_id": token_id, "payload": payload, "filename": filename}
        )
    else:
        form.only_mapped_products.data = False

    return render_template(
        "report_department_sales_forecast.html",
        form=form,
        state_token=state_token,
        filename=filename,
        product_entries=product_entries,
        pending_entries=pending_entries,
        resolved_entries=resolved_entries,
        product_search_options=product_search_options,
        skip_selection_value=_SKIP_SELECTION_VALUE,
        create_selection_value=_CREATE_SELECTION_VALUE,
        mapping_errors=mapping_errors,
        report_departments=report_departments,
        report_overall=report_overall,
        overall_warnings=overall_warnings,
        overall_unmapped_products=overall_unmapped_products,
        overall_skipped_products=overall_skipped_products,
        only_mapped=only_mapped,
        quick_product_forms=quick_product_forms,
    )


@report.route("/reports/vendor-invoices", methods=["GET", "POST"])
@login_required
def customer_invoice_report():
    """Form to select vendor invoice report parameters."""
    form = VendorInvoiceReportForm()
    form.customer.choices = [
        (c.id, f"{c.first_name} {c.last_name}") for c in Customer.query.all()
    ]

    if form.validate_on_submit():
        return redirect(
            url_for(
                "report.customer_invoice_report_results",
                customer_ids=",".join(str(id) for id in form.customer.data),
                start=form.start_date.data.isoformat(),
                end=form.end_date.data.isoformat(),
                payment_status=form.payment_status.data,
            )
        )

    return render_template("report_vendor_invoices.html", form=form)


@report.route("/reports/vendor-invoices/results")
@login_required
def customer_invoice_report_results():
    """Show vendor invoice report based on query parameters."""
    customer_ids = request.args.get("customer_ids")
    start = request.args.get("start")
    end = request.args.get("end")
    payment_status = request.args.get("payment_status", "all")
    if payment_status not in {"all", "paid", "unpaid"}:
        payment_status = "all"

    # Convert comma-separated IDs to list of ints
    id_list = [int(cid) for cid in customer_ids.split(",") if cid.isdigit()]
    customers = Customer.query.filter(Customer.id.in_(id_list)).all()

    invoice_query = Invoice.query.filter(
        Invoice.customer_id.in_(id_list),
        Invoice.date_created >= start,
        Invoice.date_created <= end,
    )

    if payment_status == "paid":
        invoice_query = invoice_query.filter(Invoice.is_paid.is_(True))
    elif payment_status == "unpaid":
        invoice_query = invoice_query.filter(Invoice.is_paid.is_(False))

    invoices = invoice_query.all()

    # Compute totals with proper GST/PST logic
    enriched_invoices = []
    for invoice in invoices:
        subtotal = 0
        gst_total = 0
        pst_total = 0

        for item in invoice.products:
            line_base = _compute_vendor_invoice_line_base(invoice, item)
            subtotal += line_base

            apply_gst = (
                item.override_gst
                if item.override_gst is not None
                else not invoice.customer.gst_exempt
            )
            apply_pst = (
                item.override_pst
                if item.override_pst is not None
                else not invoice.customer.pst_exempt
            )

            if apply_gst:
                gst_total += line_base * 0.05
            if apply_pst:
                pst_total += line_base * 0.07

        enriched_invoices.append(
            {"invoice": invoice, "total": subtotal + gst_total + pst_total}
        )

    return render_template(
        "report_vendor_invoice_results.html",
        customers=customers,
        invoices=enriched_invoices,
        start=start,
        end=end,
        payment_status=payment_status,
    )


@report.route("/reports/received-invoices", methods=["GET", "POST"])
@login_required
def received_invoice_report():
    """Display and process the received invoices report form."""

    form = ReceivedInvoiceReportForm()

    if form.validate_on_submit():
        start = form.start_date.data
        end = form.end_date.data

        if end < start:
            form.end_date.errors.append(
                "End date must be on or after the start date."
            )
            return render_template("report_received_invoices.html", form=form)

        invoice_rows = (
            db.session.query(
                PurchaseInvoice,
                PurchaseOrder.order_date.label("order_date"),
                User.email.label("received_by"),
            )
            .join(PurchaseOrder, PurchaseInvoice.purchase_order)
            .join(User, User.id == PurchaseInvoice.user_id)
            .filter(PurchaseInvoice.received_date >= start)
            .filter(PurchaseInvoice.received_date <= end)
            .order_by(PurchaseInvoice.received_date.asc(), PurchaseInvoice.id.asc())
            .all()
        )

        results = [
            {
                "invoice": invoice,
                "order_date": order_date,
                "received_by": received_by,
            }
            for invoice, order_date, received_by in invoice_rows
        ]

        return render_template(
            "report_received_invoices_results.html",
            form=form,
            results=results,
            start=start,
            end=end,
        )

    return render_template("report_received_invoices.html", form=form)


@report.route("/reports/purchase-inventory-summary", methods=["GET", "POST"])
@login_required
def purchase_inventory_summary():
    """Summarize purchased inventory quantities and spend for a date range."""

    form = PurchaseInventorySummaryForm()
    results = None
    totals = None
    start = None
    end = None
    selected_item_names = []
    selected_gl_labels = []

    if form.validate_on_submit():
        start = form.start_date.data
        end = form.end_date.data

        if end < start:
            form.end_date.errors.append(
                "End date must be on or after the start date."
            )
        else:
            query = (
                PurchaseInvoiceItem.query.join(PurchaseInvoice)
                .options(
                    selectinload(PurchaseInvoiceItem.invoice),
                    selectinload(PurchaseInvoiceItem.item),
                    selectinload(PurchaseInvoiceItem.unit),
                    selectinload(PurchaseInvoiceItem.purchase_gl_code),
                )
                .filter(PurchaseInvoice.received_date >= start)
                .filter(PurchaseInvoice.received_date <= end)
            )

            if form.items.data:
                query = query.filter(
                    PurchaseInvoiceItem.item_id.in_(form.items.data)
                )

            invoice_items = query.all()
            selected_gl_codes = set(form.gl_codes.data or [])
            aggregates = {}
            conversions = _get_base_unit_conversions()

            for inv_item in invoice_items:
                invoice = inv_item.invoice
                location_id = inv_item.location_id or (invoice.location_id if invoice else None)
                resolved_gl = inv_item.resolved_purchase_gl_code(location_id)
                gl_id = resolved_gl.id if resolved_gl else None

                if selected_gl_codes:
                    if gl_id is None:
                        if -1 not in selected_gl_codes:
                            continue
                    elif gl_id not in selected_gl_codes:
                        continue

                if inv_item.item and inv_item.unit:
                    quantity = inv_item.quantity * inv_item.unit.factor
                    unit_name = inv_item.item.base_unit or inv_item.unit.name
                elif inv_item.item:
                    quantity = inv_item.quantity
                    unit_name = inv_item.item.base_unit or (
                        inv_item.unit_name or ""
                    )
                else:
                    quantity = inv_item.quantity
                    unit_name = inv_item.unit_name or ""

                item_name = (
                    inv_item.item.name if inv_item.item else inv_item.item_name
                )
                key = (
                    inv_item.item_id
                    if inv_item.item_id is not None
                    else f"missing-{item_name}"
                )
                gl_key = gl_id if gl_id is not None else -1
                aggregate_key = (key, gl_key)

                if aggregate_key not in aggregates:
                    gl_code = (
                        resolved_gl.code
                        if resolved_gl and resolved_gl.code
                        else "Unassigned"
                    )
                    gl_description = (
                        resolved_gl.description if resolved_gl else ""
                    )
                    aggregates[aggregate_key] = {
                        "item_name": item_name,
                        "gl_code": gl_code,
                        "gl_description": gl_description,
                        "total_quantity": 0.0,
                        "unit_name": unit_name,
                        "_unit_key": unit_name,
                        "total_spend": 0.0,
                    }

                entry = aggregates[aggregate_key]
                entry["total_quantity"] += quantity
                entry["total_spend"] += inv_item.quantity * abs(inv_item.cost)
                if not entry.get("_unit_key") and unit_name:
                    entry["_unit_key"] = unit_name

            for entry in aggregates.values():
                unit_key = entry.get("_unit_key") or ""
                quantity, report_unit = convert_quantity_for_reporting(
                    entry["total_quantity"], unit_key, conversions
                )
                entry["total_quantity"] = quantity
                entry["unit_name"] = get_unit_label(report_unit)

            results = sorted(
                aggregates.values(),
                key=lambda row: (row["item_name"].lower(), row["gl_code"]),
            )

            totals = {
                "quantity": sum(row["total_quantity"] for row in results),
                "spend": sum(row["total_spend"] for row in results),
            }

            selected_item_ids = set(form.items.data or [])
            if selected_item_ids:
                selected_item_names = [
                    label
                    for value, label in form.items.choices
                    if value in selected_item_ids
                ]

            if selected_gl_codes:
                selected_gl_labels = [
                    label
                    for value, label in form.gl_codes.choices
                    if value in selected_gl_codes
                ]

    return render_template(
        "report_purchase_inventory_summary.html",
        form=form,
        results=results,
        totals=totals,
        start=start,
        end=end,
        selected_item_names=selected_item_names,
        selected_gl_labels=selected_gl_labels,
    )


@report.route("/reports/inventory-variance", methods=["GET", "POST"])
@login_required
def inventory_variance_report():
    """Compare purchased inventory against recorded usage to highlight variances."""

    form = InventoryVarianceReportForm()
    results = None
    totals = None
    start = None
    end = None
    selected_item_names: list[str] = []
    selected_gl_labels: list[str] = []

    if form.validate_on_submit():
        start = form.start_date.data
        end = form.end_date.data

        if end < start:
            form.end_date.errors.append(
                "End date must be on or after the start date."
            )
        else:
            selected_item_ids = set(form.items.data or [])
            selected_gl_codes = set(form.gl_codes.data or [])
            conversions = _get_base_unit_conversions()

            purchase_query = (
                PurchaseInvoiceItem.query.join(PurchaseInvoice)
                .options(
                    selectinload(PurchaseInvoiceItem.invoice),
                    selectinload(PurchaseInvoiceItem.item),
                    selectinload(PurchaseInvoiceItem.unit),
                    selectinload(PurchaseInvoiceItem.purchase_gl_code),
                )
                .filter(PurchaseInvoice.received_date >= start)
                .filter(PurchaseInvoice.received_date <= end)
            )

            if selected_item_ids:
                purchase_query = purchase_query.filter(
                    PurchaseInvoiceItem.item_id.in_(selected_item_ids)
                )

            purchase_items = purchase_query.all()
            purchases: dict[tuple[object, int], dict] = {}
            purchases_by_item: dict[object, list[dict]] = {}

            for inv_item in purchase_items:
                invoice = inv_item.invoice
                location_id = inv_item.location_id or (invoice.location_id if invoice else None)
                resolved_gl = inv_item.resolved_purchase_gl_code(location_id)
                gl_id = resolved_gl.id if resolved_gl else None

                if selected_gl_codes:
                    if gl_id is None:
                        if -1 not in selected_gl_codes:
                            continue
                    elif gl_id not in selected_gl_codes:
                        continue

                if inv_item.item and inv_item.unit:
                    quantity = inv_item.quantity * inv_item.unit.factor
                    unit_key = inv_item.item.base_unit or inv_item.unit.name
                elif inv_item.item:
                    quantity = inv_item.quantity
                    unit_key = inv_item.item.base_unit or (inv_item.unit_name or "")
                else:
                    quantity = inv_item.quantity
                    unit_key = inv_item.unit_name or ""

                item_name = inv_item.item.name if inv_item.item else inv_item.item_name
                item_key = (
                    inv_item.item_id
                    if inv_item.item_id is not None
                    else f"missing-{item_name}"
                )
                gl_key = gl_id if gl_id is not None else -1
                aggregate_key = (item_key, gl_key)

                entry = purchases.get(aggregate_key)
                if entry is None:
                    gl_code = (
                        resolved_gl.code
                        if resolved_gl and resolved_gl.code
                        else "Unassigned"
                    )
                    gl_description = resolved_gl.description if resolved_gl else ""
                    entry = {
                        "item_key": item_key,
                        "item_id": inv_item.item_id,
                        "item_name": item_name,
                        "gl_code": gl_code,
                        "gl_description": gl_description,
                        "gl_id": gl_id,
                        "raw_quantity": 0.0,
                        "unit_key": unit_key,
                        "purchased_value": 0.0,
                    }
                    purchases[aggregate_key] = entry
                    purchases_by_item.setdefault(item_key, []).append(entry)

                entry["raw_quantity"] += float(quantity or 0.0)
                entry["purchased_value"] += float(
                    (inv_item.quantity or 0.0) * abs(inv_item.cost or 0.0)
                )
                if not entry.get("unit_key") and unit_key:
                    entry["unit_key"] = unit_key

            for entry in purchases.values():
                unit_key = entry.get("unit_key") or ""
                quantity, report_unit = convert_quantity_for_reporting(
                    entry["raw_quantity"], unit_key, conversions
                )
                entry["purchased_quantity"] = float(quantity or 0.0)
                entry["report_unit"] = report_unit
                entry["unit_label"] = get_unit_label(report_unit)

            usage_query = (
                db.session.query(
                    Item.id.label("item_id"),
                    Item.name.label("item_name"),
                    Item.base_unit.label("base_unit"),
                    Item.cost.label("item_cost"),
                    db.func.sum(
                        InvoiceProduct.quantity
                        * ProductRecipeItem.quantity
                        * db.func.coalesce(ItemUnit.factor, 1)
                    ).label("total_quantity"),
                )
                .join(ProductRecipeItem, ProductRecipeItem.item_id == Item.id)
                .join(Product, Product.id == ProductRecipeItem.product_id)
                .join(
                    InvoiceProduct,
                    or_(
                        InvoiceProduct.product_id == Product.id,
                        and_(
                            InvoiceProduct.product_id.is_(None),
                            InvoiceProduct.product_name == Product.name,
                        ),
                    ),
                )
                .join(Invoice, Invoice.id == InvoiceProduct.invoice_id)
                .outerjoin(ItemUnit, ItemUnit.id == ProductRecipeItem.unit_id)
                .filter(
                    Invoice.date_created >= start,
                    Invoice.date_created <= end,
                )
            )

            if selected_item_ids:
                usage_query = usage_query.filter(Item.id.in_(selected_item_ids))

            usage_rows = (
                usage_query.group_by(Item.id)
                .order_by(Item.name)
                .all()
            )

            usage_totals: dict[int, dict] = {}
            for usage_row in usage_rows:
                quantity = float(usage_row.total_quantity or 0.0)
                base_unit = usage_row.base_unit or ""
                quantity, report_unit = convert_quantity_for_reporting(
                    quantity, base_unit, conversions
                )
                unit_cost = convert_cost_for_reporting(
                    float(usage_row.item_cost or 0.0), base_unit, conversions
                )
                total_cost = float(quantity or 0.0) * float(unit_cost or 0.0)

                usage_totals[usage_row.item_id] = {
                    "item_name": usage_row.item_name,
                    "used_quantity": float(quantity or 0.0),
                    "used_value": float(total_cost or 0.0),
                    "report_unit": report_unit,
                    "unit_label": get_unit_label(report_unit),
                }

            spoilage_by_key: dict[tuple[object, int], dict] = {}
            to_location = db.aliased(Location)
            transfer_start = datetime.combine(start, datetime.min.time())
            transfer_end = datetime.combine(end, datetime.max.time())

            spoilage_rows = (
                db.session.query(
                    TransferItem.item_id.label("item_id"),
                    Item.name.label("item_name"),
                    Item.base_unit.label("base_unit"),
                    Item.cost.label("item_cost"),
                    func.sum(TransferItem.quantity).label("total_quantity"),
                    LocationStandItem.purchase_gl_code_id.label("stand_gl_id"),
                    Item.purchase_gl_code_id.label("item_gl_id"),
                )
                .join(Transfer, TransferItem.transfer_id == Transfer.id)
                .join(Item, TransferItem.item_id == Item.id)
                .join(to_location, Transfer.to_location_id == to_location.id)
                .outerjoin(
                    LocationStandItem,
                    and_(
                        LocationStandItem.location_id == Transfer.from_location_id,
                        LocationStandItem.item_id == TransferItem.item_id,
                    ),
                )
                .filter(
                    Transfer.completed.is_(True),
                    to_location.is_spoilage.is_(True),
                    Transfer.date_created >= transfer_start,
                    Transfer.date_created <= transfer_end,
                )
                .group_by(
                    TransferItem.item_id,
                    Item.name,
                    Item.base_unit,
                    Item.cost,
                    LocationStandItem.purchase_gl_code_id,
                    Item.purchase_gl_code_id,
                )
                .all()
            )

            for spoilage_row in spoilage_rows:
                item_id = spoilage_row.item_id
                if item_id is None:
                    continue
                if selected_item_ids and item_id not in selected_item_ids:
                    continue

                gl_id = spoilage_row.stand_gl_id or spoilage_row.item_gl_id
                if selected_gl_codes:
                    if gl_id is None:
                        if -1 not in selected_gl_codes:
                            continue
                    elif gl_id not in selected_gl_codes:
                        continue

                base_unit = spoilage_row.base_unit or ""
                total_quantity = float(spoilage_row.total_quantity or 0.0)
                converted_qty, report_unit = convert_quantity_for_reporting(
                    total_quantity, base_unit, conversions
                )
                converted_qty = float(converted_qty or 0.0)
                unit_cost = convert_cost_for_reporting(
                    float(spoilage_row.item_cost or 0.0), base_unit, conversions
                )
                spoilage_value = converted_qty * float(unit_cost or 0.0)

                item_key = item_id
                gl_key = gl_id if gl_id is not None else -1
                aggregate_key = (item_key, gl_key)
                entry = spoilage_by_key.setdefault(
                    aggregate_key,
                    {
                        "item_key": item_key,
                        "item_id": item_id,
                        "item_name": spoilage_row.item_name,
                        "gl_id": gl_id,
                        "gl_code": None,
                        "gl_description": "",
                        "report_unit": report_unit,
                        "unit_label": get_unit_label(report_unit),
                        "spoilage_quantity": 0.0,
                        "spoilage_value": 0.0,
                    },
                )
                entry["spoilage_quantity"] += converted_qty
                entry["spoilage_value"] += spoilage_value

            gl_ids_to_load = {
                entry["gl_id"]
                for entry in spoilage_by_key.values()
                if entry["gl_id"] is not None
            }
            gl_lookup = {}
            if gl_ids_to_load:
                gl_rows = GLCode.query.filter(GLCode.id.in_(gl_ids_to_load)).all()
                gl_lookup = {gl.id: gl for gl in gl_rows}

            for entry in spoilage_by_key.values():
                gl_id = entry["gl_id"]
                if gl_id is not None:
                    gl = gl_lookup.get(gl_id)
                    entry["gl_code"] = gl.code if gl and gl.code else "Unassigned"
                    entry["gl_description"] = gl.description if gl else ""
                else:
                    entry["gl_code"] = "Unassigned"
                    entry["gl_description"] = ""

            for aggregate_key, spoilage_entry in spoilage_by_key.items():
                if aggregate_key in purchases:
                    continue
                item_key, _ = aggregate_key
                purchases_by_item.setdefault(item_key, []).append(
                    {
                        "item_key": spoilage_entry["item_key"],
                        "item_id": spoilage_entry["item_id"],
                        "item_name": spoilage_entry["item_name"],
                        "gl_code": spoilage_entry["gl_code"],
                        "gl_description": spoilage_entry["gl_description"],
                        "gl_id": spoilage_entry["gl_id"],
                        "purchased_quantity": 0.0,
                        "purchased_value": 0.0,
                        "report_unit": spoilage_entry["report_unit"],
                        "unit_label": spoilage_entry["unit_label"],
                    }
                )

            results = []
            totals = {
                "purchased_quantity": 0.0,
                "purchased_value": 0.0,
                "used_quantity": 0.0,
                "used_value": 0.0,
                "spoilage_quantity": 0.0,
                "spoilage_value": 0.0,
                "net_quantity": 0.0,
                "net_value": 0.0,
            }

            allocated_usage_items: set[int] = set()

            def _add_row(row: dict):
                results.append(row)
                totals["purchased_quantity"] += row["purchased_quantity"]
                totals["purchased_value"] += row["purchased_value"]
                totals["used_quantity"] += row["used_quantity"]
                totals["used_value"] += row["used_value"]
                totals["spoilage_quantity"] += row["spoilage_quantity"]
                totals["spoilage_value"] += row["spoilage_value"]
                totals["net_quantity"] += row["net_quantity"]
                totals["net_value"] += row["net_value"]

            for item_key, entry_list in purchases_by_item.items():
                first_entry = entry_list[0]
                item_id = first_entry.get("item_id")
                usage_entry = usage_totals.get(item_id) if item_id is not None else None

                total_usage_quantity = usage_entry["used_quantity"] if usage_entry else 0.0
                total_usage_value = usage_entry["used_value"] if usage_entry else 0.0
                total_purchase_quantity = sum(
                    entry.get("purchased_quantity", 0.0) for entry in entry_list
                )
                total_purchase_value = sum(
                    entry.get("purchased_value", 0.0) for entry in entry_list
                )

                if usage_entry and item_id is not None:
                    allocated_usage_items.add(item_id)

                for entry in entry_list:
                    aggregate_key = (
                        entry.get("item_key"),
                        entry.get("gl_id") if entry.get("gl_id") is not None else -1,
                    )
                    spoilage_entry = spoilage_by_key.pop(aggregate_key, None)
                    spoilage_quantity = (
                        float(spoilage_entry["spoilage_quantity"])
                        if spoilage_entry
                        else 0.0
                    )
                    spoilage_value = (
                        float(spoilage_entry["spoilage_value"])
                        if spoilage_entry
                        else 0.0
                    )
                    if spoilage_entry and not entry.get("unit_label"):
                        entry["unit_label"] = spoilage_entry.get("unit_label", "")

                    if total_usage_quantity > 0 and total_purchase_quantity > 0:
                        weight = (
                            entry.get("purchased_quantity", 0.0) / total_purchase_quantity
                        )
                    elif total_usage_value > 0 and total_purchase_value > 0:
                        weight = (
                            entry.get("purchased_value", 0.0) / total_purchase_value
                        )
                    elif total_usage_quantity > 0:
                        weight = 1.0 / len(entry_list)
                    else:
                        weight = 0.0

                    used_quantity = total_usage_quantity * weight
                    used_value = total_usage_value * weight
                    unit_label = entry.get("unit_label") or (
                        usage_entry.get("unit_label") if usage_entry else ""
                    )

                    row = {
                        "item_name": entry.get("item_name") or "",
                        "gl_code": entry.get("gl_code") or "Unassigned",
                        "gl_description": entry.get("gl_description") or "",
                        "unit_name": unit_label,
                        "purchased_quantity": entry.get("purchased_quantity", 0.0),
                        "purchased_value": entry.get("purchased_value", 0.0),
                        "used_quantity": used_quantity,
                        "used_value": used_value,
                        "spoilage_quantity": spoilage_quantity,
                        "spoilage_value": spoilage_value,
                        "net_quantity": entry.get("purchased_quantity", 0.0)
                        - used_quantity
                        - spoilage_quantity,
                        "net_value": entry.get("purchased_value", 0.0)
                        - used_value
                        - spoilage_value,
                    }

                    _add_row(row)

            if not results:
                results = []

            for item_id, usage_entry in usage_totals.items():
                if item_id in allocated_usage_items:
                    continue
                if selected_gl_codes and -1 not in selected_gl_codes:
                    continue

                row = {
                    "item_name": usage_entry.get("item_name") or "",
                    "gl_code": "Unassigned",
                    "gl_description": "",
                    "unit_name": usage_entry.get("unit_label") or "",
                    "purchased_quantity": 0.0,
                    "purchased_value": 0.0,
                    "used_quantity": usage_entry.get("used_quantity", 0.0),
                    "used_value": usage_entry.get("used_value", 0.0),
                    "spoilage_quantity": 0.0,
                    "spoilage_value": 0.0,
                    "net_quantity": -usage_entry.get("used_quantity", 0.0),
                    "net_value": -usage_entry.get("used_value", 0.0),
                }

                _add_row(row)

            for spoilage_entry in spoilage_by_key.values():
                row = {
                    "item_name": spoilage_entry.get("item_name") or "",
                    "gl_code": spoilage_entry.get("gl_code") or "Unassigned",
                    "gl_description": spoilage_entry.get("gl_description") or "",
                    "unit_name": spoilage_entry.get("unit_label") or "",
                    "purchased_quantity": 0.0,
                    "purchased_value": 0.0,
                    "used_quantity": 0.0,
                    "used_value": 0.0,
                    "spoilage_quantity": spoilage_entry.get("spoilage_quantity", 0.0),
                    "spoilage_value": spoilage_entry.get("spoilage_value", 0.0),
                    "net_quantity": -spoilage_entry.get("spoilage_quantity", 0.0),
                    "net_value": -spoilage_entry.get("spoilage_value", 0.0),
                }

                _add_row(row)

            results.sort(
                key=lambda row: (
                    (row.get("item_name") or "").lower(),
                    row.get("gl_code") or "",
                )
            )

            if selected_item_ids:
                selected_item_names = [
                    label
                    for value, label in form.items.choices
                    if value in selected_item_ids
                ]

            if selected_gl_codes:
                selected_gl_labels = [
                    label
                    for value, label in form.gl_codes.choices
                    if value in selected_gl_codes
                ]

    return render_template(
        "report_inventory_variance.html",
        form=form,
        results=results,
        totals=totals,
        start=start,
        end=end,
        selected_item_names=selected_item_names,
        selected_gl_labels=selected_gl_labels,
    )


def _invoice_gl_code_rows(invoice: PurchaseInvoice):
    buckets: Dict[str, Dict[str, Decimal]] = {}

    for item in invoice.items:
        line_location_id = item.location_id or invoice.location_id
        gl = item.resolved_purchase_gl_code(line_location_id)
        if gl is not None:
            code_key = gl.code
            display_code = gl.code
            description = gl.description or ""
        else:
            code_key = "__unassigned__"
            display_code = "Unassigned"
            description = ""

        entry = buckets.setdefault(
            code_key,
            {
                "code": display_code,
                "description": description,
                "base_amount": Decimal("0.00"),
                "delivery": Decimal("0.00"),
                "pst": Decimal("0.00"),
                "gst": Decimal("0.00"),
            },
        )

        # Keep GL base amounts aligned with invoice line totals so report totals
        # always reconcile to the invoice totals shown on the invoice screen.
        line_total = _quantize(_to_decimal(item.line_total))
        entry["base_amount"] += line_total

    if not buckets:
        buckets["__unassigned__"] = {
            "code": "Unassigned",
            "description": "",
            "base_amount": Decimal("0.00"),
            "delivery": Decimal("0.00"),
            "pst": Decimal("0.00"),
            "gst": Decimal("0.00"),
        }

    gst_code = "102702"
    gst_gl = GLCode.query.filter_by(code=gst_code).first()
    gst_entry = buckets.get(gst_code)
    if gst_entry is None:
        buckets[gst_code] = {
            "code": gst_code,
            "description": (gst_gl.description if gst_gl else ""),
            "base_amount": Decimal("0.00"),
            "delivery": Decimal("0.00"),
            "pst": Decimal("0.00"),
            "gst": Decimal("0.00"),
        }
        gst_entry = buckets[gst_code]
    elif gst_gl and not gst_entry.get("description"):
        gst_entry["description"] = gst_gl.description

    pst_total = _quantize(_to_decimal(invoice.pst))
    delivery_total = _quantize(_to_decimal(invoice.delivery_charge))
    gst_total = _quantize(_to_decimal(invoice.gst))

    proration_weights = {
        key: data["base_amount"]
        for key, data in buckets.items()
        if key != gst_code and data["base_amount"] > 0
    }

    if (not proration_weights) and (pst_total > 0 or delivery_total > 0):
        proration_weights = {"__unassigned__": Decimal("1.00")}
        if "__unassigned__" not in buckets:
            buckets["__unassigned__"] = {
                "code": "Unassigned",
                "description": "",
                "base_amount": Decimal("0.00"),
                "delivery": Decimal("0.00"),
                "pst": Decimal("0.00"),
                "gst": Decimal("0.00"),
            }

    pst_allocations = _allocate_amount(pst_total, proration_weights)
    delivery_allocations = _allocate_amount(delivery_total, proration_weights)

    rows = []
    totals = {
        "base_amount": Decimal("0.00"),
        "delivery": Decimal("0.00"),
        "pst": Decimal("0.00"),
        "gst": Decimal("0.00"),
        "total": Decimal("0.00"),
    }

    for key in sorted(
        buckets.keys(), key=lambda c: (c == gst_code, c == "__unassigned__", c)
    ):
        data = buckets[key]
        data["pst"] = pst_allocations.get(key, Decimal("0.00"))
        data["delivery"] = delivery_allocations.get(key, Decimal("0.00"))
        if key == gst_code:
            data["gst"] = gst_total

        line_total = (
            data["base_amount"]
            + data["delivery"]
            + data["pst"]
            + data["gst"]
        )
        line_total = _quantize(line_total)

        totals["base_amount"] += data["base_amount"]
        totals["delivery"] += data["delivery"]
        totals["pst"] += data["pst"]
        totals["gst"] += data["gst"]
        totals["total"] += line_total

        rows.append(
            {
                "code": data["code"],
                "description": data["description"],
                "base_amount": data["base_amount"],
                "delivery": data["delivery"],
                "pst": data["pst"],
                "gst": data["gst"],
                "total": line_total,
            }
        )

    totals = {key: _quantize(value) for key, value in totals.items()}

    return rows, totals


@report.route("/reports/purchase-invoices/<int:invoice_id>/gl-code")
@login_required
def invoice_gl_code_report(invoice_id: int):
    """Display the GL code allocation report for a purchase invoice."""

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

    return render_template(
        "report_invoice_gl_code.html",
        invoice=invoice,
        rows=rows,
        totals=totals,
    )


@report.route("/reports/product-sales", methods=["GET", "POST"])
@login_required
def product_sales_report():
    """Generate a report on product sales and profit."""
    form = ProductSalesReportForm()
    product_choices = list(form.products.choices)
    gl_code_choices = list(form.gl_codes.choices)
    report_data = None
    totals = None
    start = None
    end = None
    selected_product_names = []
    selected_gl_labels = []

    if form.validate_on_submit():
        start = form.start_date.data
        end = form.end_date.data

        if end < start:
            form.end_date.errors.append(
                "End date must be on or after the start date."
            )
        else:
            selected_product_ids = form.products.data or []
            selected_gl_code_ids = form.gl_codes.data or []

            products_query = (
                db.session.query(
                    Product.id,
                    Product.name,
                    Product.cost,
                    Product.price,
                    db.func.sum(InvoiceProduct.quantity).label("total_quantity"),
                )
                .join(InvoiceProduct, InvoiceProduct.product_id == Product.id)
                .join(Invoice, Invoice.id == InvoiceProduct.invoice_id)
                .filter(Invoice.date_created >= start, Invoice.date_created <= end)
            )

            if selected_product_ids:
                products_query = products_query.filter(
                    Product.id.in_(selected_product_ids)
                )

            if selected_gl_code_ids:
                included_ids = [gid for gid in selected_gl_code_ids if gid != -1]
                conditions = []
                if included_ids:
                    conditions.append(Product.sales_gl_code_id.in_(included_ids))
                if -1 in selected_gl_code_ids:
                    conditions.append(Product.sales_gl_code_id.is_(None))
                if conditions:
                    products_query = products_query.filter(or_(*conditions))

            products = (
                products_query.group_by(Product.id).order_by(Product.name).all()
            )

            report_data = []
            total_quantity = 0.0
            total_revenue = 0.0
            total_profit = 0.0
            total_cost = 0.0

            for product_row in products:
                quantity = float(product_row.total_quantity or 0.0)
                cost = float(product_row.cost or 0.0)
                price = float(product_row.price or 0.0)
                profit_each = price - cost
                total_item_cost = quantity * cost
                revenue = quantity * price
                profit = quantity * profit_each

                total_quantity += quantity
                total_cost += total_item_cost
                total_revenue += revenue
                total_profit += profit

                report_data.append(
                    {
                        "id": product_row.id,
                        "name": product_row.name,
                        "quantity": quantity,
                        "cost": cost,
                        "price": price,
                        "total_cost": total_item_cost,
                        "profit_each": profit_each,
                        "revenue": revenue,
                        "profit": profit,
                    }
                )

            totals = {
                "quantity": total_quantity,
                "cost": total_cost,
                "revenue": total_revenue,
                "profit": total_profit,
            }

            visible_product_ids = {row["id"] for row in report_data}

            if selected_product_ids:
                selected_product_names = [
                    label
                    for value, label in product_choices
                    if value in selected_product_ids
                ]
                form.products.choices = [
                    choice
                    for choice in product_choices
                    if choice[0] in selected_product_ids
                ]
            else:
                form.products.choices = (
                    [
                        choice
                        for choice in product_choices
                        if choice[0] in visible_product_ids
                    ]
                    if visible_product_ids
                    else product_choices
                )

            if selected_gl_code_ids:
                selected_gl_labels = [
                    label
                    for value, label in gl_code_choices
                    if value in selected_gl_code_ids
                ]

    return render_template(
        "report_product_sales.html",
        form=form,
        report=report_data,
        totals=totals,
        start=start,
        end=end,
        selected_product_names=selected_product_names,
        selected_gl_labels=selected_gl_labels,
    )


@report.route("/reports/product-stock-usage", methods=["GET", "POST"])
@login_required
def product_stock_usage_report():
    """Report showing stock items consumed by product sales."""

    form = ProductSalesReportForm()
    product_choices = list(form.products.choices)
    gl_code_choices = list(form.gl_codes.choices)
    report_data = None
    totals = None
    start = None
    end = None
    selected_product_names = []
    selected_gl_labels = []
    excluded_occurrences = []
    selected_payment_status = "all"

    if form.validate_on_submit():
        start = form.start_date.data
        end = form.end_date.data
        selected_payment_status = form.payment_status.data or "all"
        if selected_payment_status not in {"all", "paid", "unpaid"}:
            selected_payment_status = "all"
        form.payment_status.data = selected_payment_status

        if end < start:
            form.end_date.errors.append(
                "End date must be on or after the start date."
            )
        else:
            selected_product_ids = form.products.data or []
            selected_gl_code_ids = form.gl_codes.data or []

            items_query = (
                db.session.query(
                    Item.id.label("item_id"),
                    Item.name.label("item_name"),
                    Item.base_unit.label("base_unit"),
                    Item.cost.label("item_cost"),
                    db.func.sum(
                        InvoiceProduct.quantity
                        * ProductRecipeItem.quantity
                        * db.func.coalesce(ItemUnit.factor, 1)
                    ).label("total_quantity"),
                )
                .join(ProductRecipeItem, ProductRecipeItem.item_id == Item.id)
                .join(Product, Product.id == ProductRecipeItem.product_id)
                .join(
                    InvoiceProduct,
                    or_(
                        InvoiceProduct.product_id == Product.id,
                        and_(
                            InvoiceProduct.product_id.is_(None),
                            InvoiceProduct.product_name == Product.name,
                        ),
                    ),
                )
                .join(Invoice, Invoice.id == InvoiceProduct.invoice_id)
                .outerjoin(ItemUnit, ItemUnit.id == ProductRecipeItem.unit_id)
                .filter(
                    Invoice.date_created >= start,
                    Invoice.date_created <= end,
                )
            )
            if selected_payment_status == "paid":
                items_query = items_query.filter(Invoice.is_paid.is_(True))
            elif selected_payment_status == "unpaid":
                items_query = items_query.filter(Invoice.is_paid.is_(False))

            if selected_product_ids:
                items_query = items_query.filter(Product.id.in_(selected_product_ids))

            if selected_gl_code_ids:
                included_ids = [gid for gid in selected_gl_code_ids if gid != -1]
                conditions = []
                if included_ids:
                    conditions.append(Product.sales_gl_code_id.in_(included_ids))
                if -1 in selected_gl_code_ids:
                    conditions.append(Product.sales_gl_code_id.is_(None))
                if conditions:
                    items_query = items_query.filter(or_(*conditions))

            items = (
                items_query.group_by(Item.id)
                .order_by(Item.name)
                .all()
            )

            excluded_query = (
                db.session.query(
                    Invoice.id.label("invoice_id"),
                    Invoice.date_created.label("invoice_date"),
                    Product.name.label("product_name"),
                    Customer.first_name.label("customer_first_name"),
                    Customer.last_name.label("customer_last_name"),
                )
                .join(InvoiceProduct, InvoiceProduct.invoice_id == Invoice.id)
                .join(
                    Product,
                    or_(
                        InvoiceProduct.product_id == Product.id,
                        and_(
                            InvoiceProduct.product_id.is_(None),
                            InvoiceProduct.product_name == Product.name,
                        ),
                    ),
                )
                .join(Customer, Customer.id == Invoice.customer_id)
                .outerjoin(
                    ProductRecipeItem,
                    ProductRecipeItem.product_id == Product.id,
                )
                .filter(
                    Invoice.date_created >= start,
                    Invoice.date_created <= end,
                    ProductRecipeItem.id.is_(None),
                )
            )
            if selected_payment_status == "paid":
                excluded_query = excluded_query.filter(Invoice.is_paid.is_(True))
            elif selected_payment_status == "unpaid":
                excluded_query = excluded_query.filter(Invoice.is_paid.is_(False))

            if selected_product_ids:
                excluded_query = excluded_query.filter(
                    Product.id.in_(selected_product_ids)
                )

            if selected_gl_code_ids:
                included_ids = [gid for gid in selected_gl_code_ids if gid != -1]
                conditions = []
                if included_ids:
                    conditions.append(Product.sales_gl_code_id.in_(included_ids))
                if -1 in selected_gl_code_ids:
                    conditions.append(Product.sales_gl_code_id.is_(None))
                if conditions:
                    excluded_query = excluded_query.filter(or_(*conditions))

            excluded_occurrences = [
                {
                    "invoice_id": row.invoice_id,
                    "invoice_date": row.invoice_date,
                    "product_name": row.product_name,
                    "customer_name": (
                        (
                            f"{(row.customer_first_name or '').strip()} "
                            f"{(row.customer_last_name or '').strip()}"
                        ).strip()
                        or "Unknown Customer"
                    ),
                }
                for row in excluded_query.order_by(
                    Invoice.date_created.desc(),
                    Invoice.id.desc(),
                ).all()
            ]

            report_data = []
            total_quantity = 0.0
            total_cost = 0.0
            conversions = _get_base_unit_conversions()

            for item_row in items:
                quantity = float(item_row.total_quantity or 0.0)
                cost_each = float(item_row.item_cost or 0.0)
                base_unit = item_row.base_unit or ""
                quantity, report_unit = convert_quantity_for_reporting(
                    quantity, base_unit, conversions
                )
                cost_each = convert_cost_for_reporting(cost_each, base_unit, conversions)
                total_item_cost = quantity * cost_each

                total_quantity += quantity
                total_cost += total_item_cost

                report_data.append(
                    {
                        "id": item_row.item_id,
                        "name": item_row.item_name,
                        "unit": get_unit_label(report_unit),
                        "quantity": quantity,
                        "cost": cost_each,
                        "total_cost": total_item_cost,
                    }
                )

            totals = {
                "quantity": total_quantity,
                "cost": total_cost,
            }

            if selected_product_ids:
                selected_product_names = [
                    label
                    for value, label in product_choices
                    if value in selected_product_ids
                ]
                form.products.choices = [
                    choice
                    for choice in product_choices
                    if choice[0] in selected_product_ids
                ]
            else:
                form.products.choices = product_choices

            if selected_gl_code_ids:
                selected_gl_labels = [
                    label
                    for value, label in gl_code_choices
                    if value in selected_gl_code_ids
                ]

    return render_template(
        "report_product_stock_usage.html",
        form=form,
        report=report_data,
        totals=totals,
        start=start,
        end=end,
        selected_product_names=selected_product_names,
        selected_gl_labels=selected_gl_labels,
        payment_status=selected_payment_status,
        excluded_occurrences=excluded_occurrences,
    )


@report.route("/reports/product-recipes", methods=["GET", "POST"])
@login_required
def product_recipe_report():
    """List products with their recipe items, price and cost."""
    search = normalize_request_text_filter(request.args.get("search"))
    selected_ids = request.form.getlist("products", type=int)
    product_choices = []

    if selected_ids:
        selected_products = Product.query.filter(Product.id.in_(selected_ids)).all()
        product_choices.extend([(p.id, p.name) for p in selected_products])

    if search:
        search_products = (
            Product.query.filter(
                build_text_match_predicate(Product.name, search, "contains")
            )
            .order_by(Product.name)
            .limit(50)
            .all()
        )
        for p in search_products:
            if (p.id, p.name) not in product_choices:
                product_choices.append((p.id, p.name))

    form = ProductRecipeReportForm(product_choices=product_choices)
    report_data = []

    if form.validate_on_submit():
        if form.select_all.data or not form.products.data:
            products = Product.query.order_by(Product.name).all()
        else:
            products = (
                Product.query.filter(Product.id.in_(form.products.data))
                .order_by(Product.name)
                .all()
            )

        for prod in products:
            recipe = []
            for ri in prod.recipe_items:
                recipe.append(
                    {
                        "item_name": ri.item.name,
                        "quantity": ri.quantity,
                        "unit": ri.unit.name if ri.unit else "",
                        "cost": (ri.item.cost or 0)
                        * ri.quantity
                        * (ri.unit.factor if ri.unit else 1),
                    }
                )
            report_data.append(
                {
                    "name": prod.name,
                    "price": prod.price,
                    "cost": prod.cost,
                    "recipe": recipe,
                }
            )

        return render_template(
            "report_product_recipe_results.html", form=form, report=report_data
        )

    return render_template("report_product_recipe.html", form=form, search=search)


@report.route("/reports/product-location-sales", methods=["GET", "POST"])
@login_required
def product_location_sales_report():
    """Report showing product sales per location and last sale date."""
    form = ProductSalesReportForm()
    report_data = None

    if form.validate_on_submit():
        start = form.start_date.data
        end = form.end_date.data

        invoice_rows = (
            db.session.query(
                InvoiceProduct.product_id.label("product_id"),
                db.func.max(Invoice.date_created).label("last_sale"),
            )
            .join(Invoice, InvoiceProduct.invoice_id == Invoice.id)
            .filter(Invoice.date_created >= start, Invoice.date_created <= end)
            .group_by(InvoiceProduct.product_id)
            .all()
        )
        invoice_data = {
            row.product_id: {"last_sale": row.last_sale} for row in invoice_rows
        }

        term_rows = (
            db.session.query(
                TerminalSale.product_id.label("product_id"),
                EventLocation.location_id.label("location_id"),
                db.func.sum(TerminalSale.quantity).label("total_quantity"),
                db.func.max(TerminalSale.sold_at).label("last_sale"),
            )
            .join(EventLocation, TerminalSale.event_location_id == EventLocation.id)
            .filter(TerminalSale.sold_at >= start, TerminalSale.sold_at <= end)
            .group_by(TerminalSale.product_id, EventLocation.location_id)
            .all()
        )

        terminal_data = {}
        location_ids = set()
        for row in term_rows:
            pid = row.product_id
            location_ids.add(row.location_id)
            data = terminal_data.setdefault(
                pid, {"locations": {}, "last_sale": row.last_sale}
            )
            data["locations"][row.location_id] = row.total_quantity
            if row.last_sale > data["last_sale"]:
                data["last_sale"] = row.last_sale

        locations = {}
        if location_ids:
            loc_objs = Location.query.filter(Location.id.in_(location_ids)).all()
            locations = {loc.id: loc.name for loc in loc_objs}

        product_ids = set(invoice_data.keys()) | set(terminal_data.keys())
        products = (
            Product.query.filter(Product.id.in_(product_ids)).order_by(Product.name).all()
            if product_ids
            else []
        )

        report_data = []
        for prod in products:
            inv_last = invoice_data.get(prod.id, {}).get("last_sale")
            term_last = terminal_data.get(prod.id, {}).get("last_sale")
            last_sale = max(
                [d for d in [inv_last, term_last] if d is not None],
                default=None,
            )
            loc_list = []
            for loc_id, qty in terminal_data.get(prod.id, {}).get("locations", {}).items():
                loc_list.append(
                    {"name": locations.get(loc_id, "Unknown"), "quantity": qty}
                )
            report_data.append(
                {"name": prod.name, "last_sale": last_sale, "locations": loc_list}
            )

    return render_template(
        "report_product_location_sales.html", form=form, report=report_data
    )


@report.route("/reports/event-terminal-sales", methods=["GET", "POST"])
@login_required
def event_terminal_sales_report():
    form = EventTerminalSalesReportForm()
    report_data = None

    if form.validate_on_submit():
        start_date = form.start_date.data
        end_date = form.end_date.data

        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())

        sale_amount = func.coalesce(TerminalSale.quantity, 0) * func.coalesce(
            Product.price, 0
        )

        event_total_sales = func.coalesce(func.sum(sale_amount), 0)

        event_totals = (
            db.session.query(
                Event.id.label("event_id"),
                Event.name.label("event_name"),
                event_total_sales.label("total_sales"),
            )
            .outerjoin(EventLocation, Event.locations)
            .outerjoin(
                TerminalSale,
                and_(
                    EventLocation.id == TerminalSale.event_location_id,
                    TerminalSale.sold_at >= start_dt,
                    TerminalSale.sold_at <= end_dt,
                ),
            )
            .outerjoin(Product, TerminalSale.product)
            .filter(Event.closed.is_(True))
            .filter(
                or_(
                    and_(Event.end_date >= start_date, Event.end_date <= end_date),
                    and_(TerminalSale.sold_at >= start_dt, TerminalSale.sold_at <= end_dt),
                )
            )
            .group_by(Event.id, Event.name)
            .order_by(event_total_sales.desc(), Event.name)
            .all()
        )

        location_totals = (
            db.session.query(
                Event.id.label("event_id"),
                Location.id.label("location_id"),
                Location.name.label("location_name"),
                func.coalesce(func.sum(sale_amount), 0).label("total_sales"),
            )
            .outerjoin(EventLocation, Event.locations)
            .outerjoin(Location, EventLocation.location)
            .outerjoin(
                TerminalSale,
                and_(
                    EventLocation.id == TerminalSale.event_location_id,
                    TerminalSale.sold_at >= start_dt,
                    TerminalSale.sold_at <= end_dt,
                ),
            )
            .outerjoin(Product, TerminalSale.product)
            .filter(Event.closed.is_(True))
            .filter(
                or_(
                    and_(Event.end_date >= start_date, Event.end_date <= end_date),
                    and_(TerminalSale.sold_at >= start_dt, TerminalSale.sold_at <= end_dt),
                )
            )
            .group_by(Event.id, Location.id, Location.name)
            .all()
        )

        location_map: dict[int, list[dict]] = {}
        for row in location_totals:
            if row.location_id is None:
                continue
            location_entries = location_map.setdefault(row.event_id, [])
            location_entries.append(
                {
                    "location_id": row.location_id,
                    "location_name": row.location_name,
                    "total_sales": row.total_sales or 0,
                }
            )

        for entries in location_map.values():
            entries.sort(key=lambda entry: entry["total_sales"], reverse=True)

        report_data = [
            {
                "event_id": row.event_id,
                "event_name": row.event_name,
                "total_sales": row.total_sales or 0,
                "locations": location_map.get(row.event_id, []),
            }
            for row in event_totals
        ]

    return render_template(
        "report_event_terminal_sales.html", form=form, report=report_data
    )


@report.route("/reports/purchase-cost-forecast", methods=["GET", "POST"])
@login_required
def purchase_cost_forecast():
    """Forecast purchase costs for inventory items over a future period."""

    form = PurchaseCostForecastForm()
    report_rows = None
    totals = {"quantity": 0.0, "cost": 0.0}
    forecast_days = None
    lookback_days = None
    history_window = None

    if form.validate_on_submit():
        forecast_days = form.forecast_period.data
        history_window = form.history_window.data
        location_id = form.location_id.data or None
        item_id = form.item_id.data or None
        purchase_gl_code_ids = [
            code_id
            for code_id in (form.purchase_gl_code_ids.data or [])
            if code_id
        ]

        if location_id == 0:
            location_id = None
        if item_id == 0:
            item_id = None

        lookback_days = max(history_window, 30)
        helper = DemandForecastingHelper(lookback_days=lookback_days)
        recommendations = helper.build_recommendations(
            location_ids=[location_id] if location_id else None,
            item_ids=[item_id] if item_id else None,
            purchase_gl_code_ids=purchase_gl_code_ids or None,
        )

        report_rows = []
        for rec in recommendations:
            if lookback_days <= 0:
                continue

            consumption_per_day = rec.base_consumption / lookback_days
            incoming_total = (
                rec.history.get("transfer_in_qty", 0.0)
                + rec.history.get("invoice_qty", 0.0)
                + rec.history.get("open_po_qty", 0.0)
            )
            incoming_per_day = incoming_total / lookback_days

            forecast_consumption = consumption_per_day * forecast_days
            forecast_incoming = incoming_per_day * forecast_days
            net_quantity = max(forecast_consumption - forecast_incoming, 0.0)

            unit_cost = rec.item.cost or 0.0
            projected_cost = net_quantity * unit_cost

            if net_quantity <= 0 and projected_cost <= 0:
                continue

            totals["quantity"] += net_quantity
            totals["cost"] += projected_cost

            report_rows.append(
                {
                    "item": rec.item,
                    "location": rec.location,
                    "consumption_per_day": consumption_per_day,
                    "incoming_per_day": incoming_per_day,
                    "forecast_consumption": forecast_consumption,
                    "forecast_incoming": forecast_incoming,
                    "net_quantity": net_quantity,
                    "unit_cost": unit_cost,
                    "projected_cost": projected_cost,
                    "last_activity": rec.history.get("last_activity_ts"),
                }
            )

        report_rows.sort(key=lambda row: row["projected_cost"], reverse=True)

    return render_template(
        "report_purchase_cost_forecast.html",
        form=form,
        report_rows=report_rows,
        totals=totals,
        forecast_days=forecast_days,
        lookback_days=lookback_days,
    )
