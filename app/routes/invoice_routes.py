import json
from datetime import datetime

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app import GST, db
from app.forms import (
    BulkInvoicePaymentForm,
    CustomerForm,
    DeleteForm,
    InvoiceFilterForm,
    InvoiceForm,
)
from app.models import Customer, Invoice, InvoiceProduct, Product
from app.utils.activity import log_activity
from app.utils.filter_state import (
    filters_to_query_args,
    get_filter_defaults,
    normalize_filters,
)
from app.utils.numeric import coerce_float
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.recipe_usage import recipe_item_base_units_per_sale
from app.utils.text import normalize_request_text_filter

invoice = Blueprint("invoice", __name__)


class InvoiceCreationError(Exception):
    """Raised when invoice creation cannot be completed safely."""


class InvoiceFilterError(Exception):
    """Raised when invoice filter query parameters are invalid."""

    def __init__(self, message, *, field="date"):
        super().__init__(message)
        self.field = field


def _parse_invoice_override_flag(raw_value):
    """Return a normalized invoice tax override flag."""

    if raw_value is None:
        return None

    normalized = str(raw_value).strip().lower()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_legacy_invoice_product_entries(raw_product_data):
    """Return legacy invoice lines from the historical delimiter format."""

    entries = []
    for raw_entry in (raw_product_data or "").removesuffix(":").split(":"):
        entry = str(raw_entry or "").strip()
        if not entry:
            continue
        parts = entry.split("?")
        if len(parts) != 4:
            continue
        product_name, quantity, override_gst, override_pst = parts
        product_name = product_name.strip()
        quantity_value = coerce_float(quantity, default=None)
        if not product_name or quantity_value is None:
            continue
        entries.append(
            {
                "line_type": "catalog",
                "product_name": product_name,
                "quantity": quantity_value,
                "override_gst": _parse_invoice_override_flag(override_gst),
                "override_pst": _parse_invoice_override_flag(override_pst),
            }
        )
    return entries


def _parse_optional_invoice_amount(raw_value, *, error_message):
    """Parse an optional currency amount, treating blanks as zero."""

    if raw_value is None:
        return 0.0

    raw_text = str(raw_value).strip()
    if not raw_text:
        return 0.0

    parsed_value = coerce_float(raw_text, default=None)
    if parsed_value is None:
        raise InvoiceCreationError(error_message)
    return parsed_value


def _parse_json_invoice_product_entries(raw_product_data):
    """Return structured invoice lines from the JSON payload format."""

    try:
        raw_entries = json.loads(raw_product_data)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise InvoiceCreationError("Invoice lines payload is invalid.")

    if not isinstance(raw_entries, list):
        raise InvoiceCreationError("Invoice lines payload is invalid.")

    entries = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise InvoiceCreationError("Invoice lines payload is invalid.")

        line_type = str(raw_entry.get("line_type") or "catalog").strip().lower()
        if line_type not in {"catalog", "custom"}:
            raise InvoiceCreationError("Invoice lines payload is invalid.")

        product_name = str(raw_entry.get("product_name") or "").strip()
        quantity_value = coerce_float(raw_entry.get("quantity"), default=None)
        if not product_name:
            raise InvoiceCreationError("Each invoice line needs a description.")
        if quantity_value is None:
            raise InvoiceCreationError("Each invoice line needs a valid quantity.")

        if line_type == "custom":
            unit_price = coerce_float(raw_entry.get("unit_price"), default=None)
            if unit_price is None:
                raise InvoiceCreationError("Each custom line needs a valid price.")

            entries.append(
                {
                    "line_type": "custom",
                    "product_name": product_name,
                    "quantity": quantity_value,
                    "unit_price": unit_price,
                    "line_gst": _parse_optional_invoice_amount(
                        raw_entry.get("line_gst"),
                        error_message=(
                            "Custom GST, PST, and fee values must be valid numbers."
                        ),
                    ),
                    "line_pst": _parse_optional_invoice_amount(
                        raw_entry.get("line_pst"),
                        error_message=(
                            "Custom GST, PST, and fee values must be valid numbers."
                        ),
                    ),
                    "additional_fee": _parse_optional_invoice_amount(
                        raw_entry.get("additional_fee"),
                        error_message=(
                            "Custom GST, PST, and fee values must be valid numbers."
                        ),
                    ),
                }
            )
            continue

        entries.append(
            {
                "line_type": "catalog",
                "product_name": product_name,
                "quantity": quantity_value,
                "override_gst": _parse_invoice_override_flag(
                    raw_entry.get("override_gst")
                ),
                "override_pst": _parse_invoice_override_flag(
                    raw_entry.get("override_pst")
                ),
            }
        )

    return entries


def _parse_invoice_product_entries(raw_product_data):
    """Return valid invoice line payloads extracted from the hidden form field."""

    raw_text = str(raw_product_data or "").strip()
    if not raw_text:
        return []
    if raw_text.startswith("["):
        return _parse_json_invoice_product_entries(raw_text)
    return _parse_legacy_invoice_product_entries(raw_text)


def _parse_invoice_filter_dates(start_date_str, end_date_str):
    start_date = None
    end_date = None

    if start_date_str:
        try:
            start_date = datetime.fromisoformat(start_date_str).date()
        except ValueError as exc:
            raise InvoiceFilterError("Invalid start date.", field="start_date") from exc
    if end_date_str:
        try:
            end_date = datetime.fromisoformat(end_date_str).date()
        except ValueError as exc:
            raise InvoiceFilterError("Invalid end date.", field="end_date") from exc
    if start_date and end_date and start_date > end_date:
        raise InvoiceFilterError(
            "Invalid date range: start cannot be after end.",
            field="end_date",
        )

    return start_date, end_date


def _normalize_invoice_status_filter(raw_status):
    normalized = str(raw_status or "all").strip().lower()
    if normalized in {
        "all",
        "unpaid",
        Invoice.STATUS_PENDING,
        Invoice.STATUS_DELIVERED,
        Invoice.STATUS_PAID,
    }:
        return normalized
    return "all"


def _apply_invoice_status_filter(query, status_filter):
    if status_filter == Invoice.STATUS_PENDING:
        return query.filter(
            Invoice.is_paid.is_(False),
            Invoice.status == Invoice.STATUS_PENDING,
        )
    if status_filter == Invoice.STATUS_DELIVERED:
        return query.filter(
            Invoice.is_paid.is_(False),
            Invoice.status == Invoice.STATUS_DELIVERED,
        )
    if status_filter == Invoice.STATUS_PAID:
        return query.filter(Invoice.is_paid.is_(True))
    if status_filter == "unpaid":
        return query.filter(Invoice.is_paid.is_(False))
    return query


def _create_invoice_from_form(form):
    customer = db.session.get(Customer, form.customer.data)
    if customer is None:
        abort(404)
    parsed_entries = _parse_invoice_product_entries(form.products.data)
    if not parsed_entries:
        raise InvoiceCreationError(
            "Add at least one valid invoice line before creating an invoice."
        )

    today = datetime.now().strftime("%d%m%y")
    count = (
        Invoice.query.filter(
            func.date(Invoice.date_created) == func.current_date(),
            Invoice.customer_id == customer.id,
        ).count()
        + 1
    )
    invoice_id = f"{customer.first_name[0]}{customer.last_name[0]}{customer.id}{today}{count:02}"

    invoice = Invoice(
        id=invoice_id,
        customer_id=customer.id,
        user_id=current_user.id,
        status=Invoice.STATUS_PENDING,
        delivered_at=None,
        is_paid=False,
        paid_at=None,
    )
    db.session.add(invoice)

    product_names = {
        entry["product_name"]
        for entry in parsed_entries
        if entry.get("line_type") == "catalog"
    }

    products = (
        Product.query.filter(Product.name.in_(product_names)).all()
        if product_names
        else []
    )
    product_lookup = {p.name: p for p in products}
    created_line_count = 0
    line_items_to_log = []

    for entry in parsed_entries:
        line_type = entry.get("line_type", "catalog")
        product_name = entry["product_name"]
        quantity = entry["quantity"]
        if line_type == "custom":
            unit_price = float(entry["unit_price"])
            additional_fee = float(entry.get("additional_fee", 0.0) or 0.0)
            line_subtotal = (quantity * unit_price) + additional_fee

            invoice_product = InvoiceProduct(
                invoice_id=invoice.id,
                product_id=None,
                is_custom_line=True,
                product_name=product_name,
                quantity=quantity,
                override_gst=None,
                override_pst=None,
                unit_price=unit_price,
                line_subtotal=line_subtotal,
                line_gst=float(entry.get("line_gst", 0.0) or 0.0),
                line_pst=float(entry.get("line_pst", 0.0) or 0.0),
            )
            db.session.add(invoice_product)
            created_line_count += 1
            line_items_to_log.append({"product_name": product_name, "quantity": quantity})
            continue

        override_gst = entry["override_gst"]
        override_pst = entry["override_pst"]
        product = product_lookup.get(product_name)

        if product:
            invoice_price = product.invoice_sale_price
            unit_price = (
                float(invoice_price)
                if invoice_price is not None
                else float(product.price)
            )
            line_subtotal = quantity * unit_price

            override_gst = (
                _parse_invoice_override_flag(override_gst)
            )
            override_pst = (
                _parse_invoice_override_flag(override_pst)
            )

            apply_gst = (
                override_gst
                if override_gst is not None
                else not customer.gst_exempt
            )
            apply_pst = (
                override_pst
                if override_pst is not None
                else not customer.pst_exempt
            )

            line_gst = line_subtotal * 0.05 if apply_gst else 0
            line_pst = line_subtotal * 0.07 if apply_pst else 0

            invoice_product = InvoiceProduct(
                invoice_id=invoice.id,
                product_id=product.id,
                is_custom_line=False,
                product_name=product.name,
                quantity=quantity,
                override_gst=override_gst,
                override_pst=override_pst,
                unit_price=unit_price,
                line_subtotal=line_subtotal,
                line_gst=line_gst,
                line_pst=line_pst,
            )
            db.session.add(invoice_product)
            created_line_count += 1
            line_items_to_log.append({"product_id": product.id, "quantity": quantity})

            product.quantity = (product.quantity or 0) - quantity

            for recipe_item in product.recipe_items:
                item = recipe_item.item
                units_per_sale = recipe_item_base_units_per_sale(recipe_item)
                if units_per_sale <= 0:
                    continue
                item.quantity = (item.quantity or 0) - (units_per_sale * quantity)

    if created_line_count == 0:
        db.session.rollback()
        raise InvoiceCreationError(
            "Add at least one valid invoice line before creating an invoice."
        )

    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        current_app.logger.exception(
            "invoice_creation_integrity_error",
            extra={
                "invoice_id": invoice.id,
                "customer_id": customer.id,
                "line_items": line_items_to_log,
            },
        )
        raise InvoiceCreationError(
            "Unable to create invoice right now. Please try again."
        ) from exc

    log_activity(f"Created invoice {invoice.id}")
    return invoice


@invoice.route("/create_invoice", methods=["GET", "POST"])
@login_required
def create_invoice():
    """Create a sales invoice."""
    form = InvoiceForm()
    customer_form = CustomerForm()
    form.customer.choices = [
        (c.id, f"{c.first_name} {c.last_name}") for c in Customer.query.all()
    ]

    if form.validate_on_submit():
        try:
            _create_invoice_from_form(form)
        except InvoiceCreationError as exc:
            flash(str(exc), "danger")
        else:
            flash("Invoice created successfully!", "success")
            return redirect(url_for("invoice.view_invoices"))

    return render_template(
        "invoices/create_invoice.html",
        form=form,
        customer_form=customer_form,
    )


@invoice.route("/delete_invoice/<invoice_id>", methods=["POST"])
@login_required
def delete_invoice(invoice_id):
    """Delete an invoice and its lines."""
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    # Retrieve the invoice object from the database
    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None:
        abort(404)
    # Delete the invoice from the database
    db.session.delete(invoice)
    db.session.commit()
    log_activity(f"Deleted invoice {invoice.id}")
    flash("Invoice deleted successfully!", "success")
    # Redirect the user to the home page or any other appropriate page
    return redirect(url_for("invoice.view_invoices"))


def _format_invoice_status_label(status):
    return {
        Invoice.STATUS_PENDING: "pending",
        Invoice.STATUS_DELIVERED: "delivered",
        Invoice.STATUS_PAID: "paid",
    }.get(status, status)


def _invoice_status_transition_error(invoice_record, target_status):
    if (
        target_status == Invoice.STATUS_PAID
        and invoice_record.invoice_status == Invoice.STATUS_PENDING
    ):
        return (
            f"Invoice {invoice_record.id} must be marked delivered before it can be "
            "marked paid."
        )
    return (
        f"Invoice {invoice_record.id} cannot be marked "
        f"{_format_invoice_status_label(target_status)} from its current status."
    )


def _set_invoice_status(invoice_id, *, target_status):
    updated_invoices, missing_invoice_ids, invalid_invoices = _apply_invoice_status(
        [invoice_id], target_status=target_status
    )
    if missing_invoice_ids:
        abort(404)
    if invalid_invoices:
        flash(
            _invoice_status_transition_error(
                invalid_invoices[0], target_status
            ),
            "danger",
        )
        return redirect(
            request.referrer
            or url_for("invoice.view_invoice", invoice_id=invalid_invoices[0].id)
        )

    invoice_record = updated_invoices[0]
    flash(
        f"Invoice {invoice_record.id} marked as "
        f"{invoice_record.invoice_status_label.lower()}.",
        "success",
    )

    return redirect(
        request.referrer
        or url_for("invoice.view_invoice", invoice_id=invoice_record.id)
    )


def _normalize_invoice_ids(raw_invoice_ids):
    if raw_invoice_ids is None:
        return []
    if isinstance(raw_invoice_ids, str):
        candidates = raw_invoice_ids.split(",")
    elif isinstance(raw_invoice_ids, (list, tuple, set)):
        candidates = []
        for value in raw_invoice_ids:
            if isinstance(value, str):
                candidates.extend(value.split(","))
            else:
                candidates.append(str(value))
    else:
        candidates = [str(raw_invoice_ids)]

    normalized = []
    seen = set()
    for candidate in candidates:
        invoice_id = str(candidate).strip()
        if not invoice_id or invoice_id in seen:
            continue
        normalized.append(invoice_id)
        seen.add(invoice_id)
    return normalized


def _parse_invoice_status_action(raw_status):
    if isinstance(raw_status, bool):
        return Invoice.STATUS_PAID if raw_status else Invoice.STATUS_DELIVERED
    if raw_status is None:
        return None

    normalized = str(raw_status).strip().lower()
    if normalized in {"true", "1", Invoice.STATUS_PAID}:
        return Invoice.STATUS_PAID
    if normalized in {"false", "0", "unpaid", Invoice.STATUS_DELIVERED}:
        return Invoice.STATUS_DELIVERED
    if normalized == Invoice.STATUS_PENDING:
        return Invoice.STATUS_PENDING
    return None


def _can_transition_invoice_status(invoice_record, target_status):
    current_status = invoice_record.invoice_status
    if target_status == Invoice.STATUS_PAID:
        return current_status in {Invoice.STATUS_DELIVERED, Invoice.STATUS_PAID}
    if target_status == Invoice.STATUS_DELIVERED:
        return current_status in {
            Invoice.STATUS_PENDING,
            Invoice.STATUS_DELIVERED,
            Invoice.STATUS_PAID,
        }
    if target_status == Invoice.STATUS_PENDING:
        return current_status in {Invoice.STATUS_PENDING, Invoice.STATUS_DELIVERED}
    return False


def _update_invoice_status(invoice_record, *, target_status, changed_at):
    if target_status == Invoice.STATUS_PENDING:
        invoice_record.status = Invoice.STATUS_PENDING
        invoice_record.delivered_at = None
        invoice_record.is_paid = False
        invoice_record.paid_at = None
        return

    if target_status == Invoice.STATUS_DELIVERED:
        invoice_record.status = Invoice.STATUS_DELIVERED
        invoice_record.delivered_at = invoice_record.delivered_at or changed_at
        invoice_record.is_paid = False
        invoice_record.paid_at = None
        return

    invoice_record.status = Invoice.STATUS_PAID
    invoice_record.delivered_at = invoice_record.delivered_at or changed_at
    invoice_record.is_paid = True
    invoice_record.paid_at = changed_at


def _apply_invoice_status(invoice_ids, *, target_status):
    normalized_invoice_ids = _normalize_invoice_ids(invoice_ids)
    if not normalized_invoice_ids:
        return [], [], []

    invoices = Invoice.query.filter(Invoice.id.in_(normalized_invoice_ids)).all()
    invoices_by_id = {invoice.id: invoice for invoice in invoices}
    missing_invoice_ids = [
        invoice_id
        for invoice_id in normalized_invoice_ids
        if invoice_id not in invoices_by_id
    ]
    if missing_invoice_ids:
        return [], missing_invoice_ids, []

    ordered_invoices = [
        invoices_by_id[invoice_id] for invoice_id in normalized_invoice_ids
    ]
    invalid_invoices = [
        invoice_record
        for invoice_record in ordered_invoices
        if not _can_transition_invoice_status(invoice_record, target_status)
    ]
    if invalid_invoices:
        return [], [], invalid_invoices

    changed_at = datetime.utcnow()
    for invoice_record in ordered_invoices:
        if invoice_record.invoice_status == target_status:
            continue
        _update_invoice_status(
            invoice_record,
            target_status=target_status,
            changed_at=changed_at,
        )

    db.session.commit()
    log_activity(
        f"Marked {len(ordered_invoices)} invoice(s) as "
        f"{_format_invoice_status_label(target_status)}"
    )

    return ordered_invoices, [], []


@invoice.route("/invoice/<invoice_id>/mark-delivered", methods=["POST"])
@login_required
def mark_invoice_delivered(invoice_id):
    """Mark an invoice as delivered."""
    return _set_invoice_status(invoice_id, target_status=Invoice.STATUS_DELIVERED)


@invoice.route("/invoice/<invoice_id>/mark-paid", methods=["POST"])
@login_required
def mark_invoice_paid(invoice_id):
    """Mark an invoice as paid and stamp payment time."""
    return _set_invoice_status(invoice_id, target_status=Invoice.STATUS_PAID)


@invoice.route("/invoice/<invoice_id>/mark-unpaid", methods=["POST"])
@login_required
def mark_invoice_unpaid(invoice_id):
    """Legacy alias that reopens a paid invoice as delivered."""
    return _set_invoice_status(invoice_id, target_status=Invoice.STATUS_DELIVERED)


@invoice.route("/invoices/bulk-payment-status", methods=["POST"])
@login_required
def bulk_invoice_payment_status():
    """Bulk-update invoice workflow status for one or more invoices."""
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
    )

    payload = request.get_json(silent=True) if request.is_json else {}
    if request.is_json:
        form = BulkInvoicePaymentForm(meta={"csrf": False})
        raw_invoice_ids = payload.get("invoice_ids", payload.get("selected_ids"))
        raw_status = payload.get(
            "status",
            payload.get("payment_status", payload.get("is_paid")),
        )
    else:
        form = BulkInvoicePaymentForm()
        raw_invoice_ids = (
            request.form.getlist("invoice_ids")
            or request.form.get("invoice_ids")
        )
        raw_status = request.form.get("status", request.form.get("is_paid"))

    normalized_invoice_ids = _normalize_invoice_ids(raw_invoice_ids)
    if normalized_invoice_ids:
        form.selected_ids.data = ",".join(normalized_invoice_ids)

    parsed_status = _parse_invoice_status_action(raw_status)
    if parsed_status == Invoice.STATUS_PAID:
        form.payment_status.data = "paid"
    elif parsed_status == Invoice.STATUS_DELIVERED:
        form.payment_status.data = "delivered"
    elif parsed_status == Invoice.STATUS_PENDING:
        form.payment_status.data = "pending"
    elif raw_status is not None:
        form.payment_status.data = str(raw_status)

    form_is_valid = form.validate() if request.is_json else form.validate_on_submit()
    if not form_is_valid:
        message = (
            form.selected_ids.errors[0]
            if form.selected_ids.errors
            else form.payment_status.errors[0]
            if form.payment_status.errors
            else "Invalid form submission."
        )
        if is_ajax:
            return {"success": False, "message": message}, 400
        flash(message, "danger")
        return redirect(request.referrer or url_for("invoice.view_invoices"))

    invoice_ids = _normalize_invoice_ids(form.selected_ids.data)
    target_status = form.payment_status.data

    updated_invoices, missing_invoice_ids, invalid_invoices = _apply_invoice_status(
        invoice_ids, target_status=target_status
    )
    if missing_invoice_ids:
        message = (
            "Some invoices were not found: "
            + ", ".join(sorted(missing_invoice_ids))
        )
        if is_ajax:
            return {
                "success": False,
                "message": message,
                "missing_invoice_ids": missing_invoice_ids,
            }, 404
        flash(message, "danger")
        return redirect(request.referrer or url_for("invoice.view_invoices"))

    if invalid_invoices:
        message = (
            "Pending invoices must be marked delivered before they can be marked "
            "paid."
            if target_status == Invoice.STATUS_PAID
            else "One or more selected invoices could not be moved to that status."
        )
        if is_ajax:
            return {
                "success": False,
                "message": message,
                "invalid_invoice_ids": [invoice.id for invoice in invalid_invoices],
            }, 400
        flash(message, "danger")
        return redirect(request.referrer or url_for("invoice.view_invoices"))

    if is_ajax:
        return {
            "success": True,
            "count": len(updated_invoices),
            "status": target_status,
            "updated": [
                {
                    "id": invoice.id,
                    "status": invoice.invoice_status,
                    "status_label": invoice.invoice_status_label,
                    "delivered_at": (
                        invoice.delivered_at.isoformat()
                        if invoice.delivered_at
                        else None
                    ),
                    "is_paid": invoice.is_paid,
                    "paid_at": (
                        invoice.paid_at.isoformat() if invoice.paid_at else None
                    ),
                    "payment_status": invoice.payment_status_label,
                }
                for invoice in updated_invoices
            ],
        }

    flash(
        f"Marked {len(updated_invoices)} invoice(s) as "
        f"{_format_invoice_status_label(target_status)}.",
        "success",
    )
    return redirect(request.referrer or url_for("invoice.view_invoices"))


@invoice.route("/view_invoice/<invoice_id>", methods=["GET"])
@login_required
def view_invoice(invoice_id):
    """Render an invoice for viewing."""
    invoice = db.session.get(Invoice, invoice_id)
    if invoice is None:
        abort(404)

    subtotal = 0
    gst_total = 0
    pst_total = 0

    invoice_lines = []
    for invoice_product in invoice.products:
        # Use stored values instead of recalculating from current product price
        line_total = invoice_product.line_subtotal
        subtotal += line_total
        gst_total += invoice_product.line_gst
        pst_total += invoice_product.line_pst
        name = (
            invoice_product.product.name
            if invoice_product.product
            else invoice_product.product_name
        )
        tax_flags = ""
        if invoice_product.line_gst > 0:
            tax_flags += "G"
        if invoice_product.line_pst > 0:
            tax_flags += "P"

        invoice_lines.append((invoice_product, name, tax_flags))

    total = subtotal + gst_total + pst_total

    return render_template(
        "invoices/view_invoice.html",
        invoice=invoice,
        invoice_lines=invoice_lines,
        subtotal=subtotal,
        gst=gst_total,
        pst=pst_total,
        total=total,
        GST=GST,
        retail_pop_price=current_app.config.get("RETAIL_POP_PRICE", "0.00"),
        delete_form=DeleteForm(),
    )


@invoice.route("/get_customer_tax_status/<int:customer_id>")
@login_required
def get_customer_tax_status(customer_id):
    """Return GST and PST exemptions for a customer."""
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        abort(404)
    return {
        "gst_exempt": customer.gst_exempt,
        "pst_exempt": customer.pst_exempt,
    }


@invoice.route("/api/filter_invoices")
@login_required
def filter_invoices_api():
    """Return invoices matching filters as JSON."""
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    invoice_id = normalize_request_text_filter(request.args.get("invoice_id"))
    customer_id = request.args.get("customer_id", type=int)
    user_id = request.args.get("user_id", type=int)
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")
    status_filter = _normalize_invoice_status_filter(
        request.args.get("status", request.args.get("payment_status", "all"))
    )

    try:
        start_date, end_date = _parse_invoice_filter_dates(
            start_date_str, end_date_str
        )
    except InvoiceFilterError as exc:
        return {"errors": {exc.field: [str(exc)]}}, 400

    query = Invoice.query
    if user_id:
        query = query.filter(Invoice.user_id == user_id)
    if invoice_id:
        query = query.filter(Invoice.id.ilike(f"%{invoice_id}%"))
    if customer_id and customer_id != -1:
        query = query.filter(Invoice.customer_id == customer_id)
    if start_date:
        query = query.filter(
            Invoice.date_created
            >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        query = query.filter(
            Invoice.date_created
            <= datetime.combine(end_date, datetime.max.time())
        )
    query = _apply_invoice_status_filter(query, status_filter)

    invoices = query.order_by(Invoice.date_created.desc()).paginate(
        page=page, per_page=per_page
    )
    data = [
        {
            "id": inv.id,
            "date": inv.date_created.strftime("%Y-%m-%d"),
            "customer": f"{inv.customer.first_name} {inv.customer.last_name}",
            "status": inv.invoice_status,
            "status_label": inv.invoice_status_label,
            "payment_status": inv.payment_status_label,
        }
        for inv in invoices.items
    ]
    return {
        "invoices": data,
        "pagination": {
            "page": invoices.page,
            "pages": invoices.pages,
            "has_prev": invoices.has_prev,
            "has_next": invoices.has_next,
            "prev_num": invoices.prev_num,
            "next_num": invoices.next_num,
            "per_page": per_page,
            "total": invoices.total,
        },
    }


@invoice.route("/api/create_invoice", methods=["POST"])
@login_required
def create_invoice_api():
    """Create an invoice via AJAX and return JSON."""
    form = InvoiceForm()
    form.customer.choices = [
        (c.id, f"{c.first_name} {c.last_name}") for c in Customer.query.all()
    ]
    if form.validate_on_submit():
        try:
            invoice = _create_invoice_from_form(form)
        except InvoiceCreationError as exc:
            return {"errors": {"products": [str(exc)]}}, 400
        customer = invoice.customer
        return {
            "invoice": {
                "id": invoice.id,
                "date": invoice.date_created.strftime("%Y-%m-%d"),
                "customer": f"{customer.first_name} {customer.last_name}",
                "status": invoice.invoice_status,
                "status_label": invoice.invoice_status_label,
            }
        }
    return {"errors": form.errors}, 400


@invoice.route("/view_invoices", methods=["GET", "POST"])
@login_required
def view_invoices():
    """List invoices with optional filters."""
    user_id = request.args.get("user_id", type=int)
    scope = request.endpoint or "invoice.view_invoices"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args,
        exclude=("page", "per_page", "reset", "user_id"),
    )
    if default_filters and not active_filters:
        redirect_args = filters_to_query_args(default_filters)
        if user_id is not None:
            redirect_args["user_id"] = str(user_id)
        return redirect(url_for("invoice.view_invoices", **redirect_args))

    form = InvoiceFilterForm()
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    form.customer_id.choices = [(-1, "All")] + [
        (c.id, f"{c.first_name} {c.last_name}")
        for c in Customer.query.order_by(Customer.last_name, Customer.first_name).all()
    ]

    invoice_id = normalize_request_text_filter(request.args.get("invoice_id"))
    customer_id = request.args.get("customer_id", type=int)
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")
    status_filter = _normalize_invoice_status_filter(
        request.args.get("status", request.args.get("payment_status", "all"))
    )
    try:
        start_date, end_date = _parse_invoice_filter_dates(
            start_date_str, end_date_str
        )
    except InvoiceFilterError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("invoice.view_invoices"))

    form.invoice_id.data = invoice_id
    if customer_id is not None:
        form.customer_id.data = customer_id
    if start_date:
        form.start_date.data = start_date
    if end_date:
        form.end_date.data = end_date

    query = Invoice.query
    if user_id:
        query = query.filter(Invoice.user_id == user_id)
    if invoice_id:
        query = query.filter(Invoice.id.ilike(f"%{invoice_id}%"))
    if customer_id and customer_id != -1:
        query = query.filter(Invoice.customer_id == customer_id)
    if start_date:
        query = query.filter(
            Invoice.date_created
            >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        query = query.filter(
            Invoice.date_created
            <= datetime.combine(end_date, datetime.max.time())
        )
    query = _apply_invoice_status_filter(query, status_filter)
    invoices = query.order_by(Invoice.date_created.desc()).paginate(
        page=page, per_page=per_page
    )
    delete_form = DeleteForm()
    create_form = InvoiceForm()
    customer_form = CustomerForm()
    create_form.customer.choices = [
        (c.id, f"{c.first_name} {c.last_name}") for c in Customer.query.all()
    ]
    return render_template(
        "invoices/view_invoices.html",
        invoices=invoices,
        form=form,
        delete_form=delete_form,
        create_form=create_form,
        customer_form=customer_form,
        per_page=per_page,
        pagination_args=build_pagination_args(
            per_page,
            extra_params={
                "invoice_id": invoice_id or None,
                "user_id": user_id,
                "customer_id": customer_id,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "payment_status": status_filter,
            },
        ),
        status_filter=status_filter,
    )
