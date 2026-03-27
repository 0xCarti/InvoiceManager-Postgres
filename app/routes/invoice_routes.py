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
    DeleteForm,
    InvoiceFilterForm,
    InvoiceForm,
)
from app.models import Customer, Invoice, InvoiceProduct, Product
from app.utils.activity import log_activity
from app.utils.numeric import coerce_float
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.text import normalize_request_text_filter

invoice = Blueprint("invoice", __name__)


class InvoiceCreationError(Exception):
    """Raised when invoice creation cannot be completed safely."""


def _create_invoice_from_form(form):
    customer = db.session.get(Customer, form.customer.data)
    if customer is None:
        abort(404)
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
        id=invoice_id, customer_id=customer.id, user_id=current_user.id
    )
    db.session.add(invoice)

    product_data = form.products.data.removesuffix(":").split(":")

    parsed_entries = []
    product_names = set()
    for entry in product_data:
        try:
            product_name, quantity, override_gst, override_pst = entry.split(
                "?"
            )
        except ValueError:
            flash(f"Invalid product data format: '{entry}'", "danger")
            continue
        parsed_entries.append(
            (product_name, quantity, override_gst, override_pst)
        )
        product_names.add(product_name)

    products = (
        Product.query.filter(Product.name.in_(product_names)).all()
        if product_names
        else []
    )
    product_lookup = {p.name: p for p in products}

    for product_name, quantity, override_gst, override_pst in parsed_entries:
        product = product_lookup.get(product_name)

        if product:
            quantity_value = coerce_float(quantity)
            if quantity_value is None:
                continue
            quantity = quantity_value
            invoice_price = product.invoice_sale_price
            unit_price = (
                float(invoice_price)
                if invoice_price is not None
                else float(product.price)
            )
            line_subtotal = quantity * unit_price

            override_gst = (
                None if override_gst == "" else bool(int(override_gst))
            )
            override_pst = (
                None if override_pst == "" else bool(int(override_pst))
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

            product.quantity = (product.quantity or 0) - quantity

            for recipe_item in product.recipe_items:
                item = recipe_item.item
                factor = recipe_item.unit.factor if recipe_item.unit else 1
                item.quantity = (item.quantity or 0) - (
                    recipe_item.quantity * factor * quantity
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
                "line_items": [
                    {"product_id": item.product_id, "quantity": item.quantity}
                    for item in invoice.products
                ],
            },
        )
        raise InvoiceCreationError("Invoice could not be created.") from exc

    log_activity(f"Created invoice {invoice.id}")
    return invoice


@invoice.route("/create_invoice", methods=["GET", "POST"])
@login_required
def create_invoice():
    """Create a sales invoice."""
    form = InvoiceForm()
    form.customer.choices = [
        (c.id, f"{c.first_name} {c.last_name}") for c in Customer.query.all()
    ]

    if form.validate_on_submit():
        try:
            _create_invoice_from_form(form)
        except InvoiceCreationError:
            flash(
                "Unable to create invoice right now. Please try again.",
                "danger",
            )
        else:
            flash("Invoice created successfully!", "success")
            return redirect(url_for("invoice.view_invoices"))

    return render_template("invoices/create_invoice.html", form=form)


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


def _set_invoice_payment_status(invoice_id, *, is_paid):
    updated_invoices, missing_invoice_ids = _apply_invoice_payment_status(
        [invoice_id], is_paid=is_paid
    )
    if missing_invoice_ids:
        abort(404)

    invoice_record = updated_invoices[0]
    status = "paid" if is_paid else "unpaid"
    flash(f"Invoice {invoice_record.id} marked as {status}.", "success")

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


def _parse_is_paid(raw_status):
    if isinstance(raw_status, bool):
        return raw_status
    if raw_status is None:
        return None

    normalized = str(raw_status).strip().lower()
    if normalized in {"true", "1", "paid"}:
        return True
    if normalized in {"false", "0", "unpaid"}:
        return False
    return None


def _apply_invoice_payment_status(invoice_ids, *, is_paid):
    normalized_invoice_ids = _normalize_invoice_ids(invoice_ids)
    if not normalized_invoice_ids:
        return [], []

    invoices = Invoice.query.filter(Invoice.id.in_(normalized_invoice_ids)).all()
    invoices_by_id = {invoice.id: invoice for invoice in invoices}
    missing_invoice_ids = [
        invoice_id
        for invoice_id in normalized_invoice_ids
        if invoice_id not in invoices_by_id
    ]
    if missing_invoice_ids:
        return [], missing_invoice_ids

    paid_at = datetime.utcnow() if is_paid else None
    for invoice in invoices:
        invoice.is_paid = is_paid
        invoice.paid_at = paid_at

    db.session.commit()
    status = "paid" if is_paid else "unpaid"
    log_activity(f"Marked {len(invoices)} invoice(s) as {status}")

    return [invoices_by_id[invoice_id] for invoice_id in normalized_invoice_ids], []


@invoice.route("/invoice/<invoice_id>/mark-paid", methods=["POST"])
@login_required
def mark_invoice_paid(invoice_id):
    """Mark an invoice as paid and stamp payment time."""
    return _set_invoice_payment_status(invoice_id, is_paid=True)


@invoice.route("/invoice/<invoice_id>/mark-unpaid", methods=["POST"])
@login_required
def mark_invoice_unpaid(invoice_id):
    """Mark an invoice as unpaid and clear payment time."""
    return _set_invoice_payment_status(invoice_id, is_paid=False)


@invoice.route("/invoices/bulk-payment-status", methods=["POST"])
@login_required
def bulk_invoice_payment_status():
    """Bulk-update payment status for one or more invoices."""
    is_ajax = (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.is_json
    )

    payload = request.get_json(silent=True) if request.is_json else {}
    if request.is_json:
        form = BulkInvoicePaymentForm(meta={"csrf": False})
        raw_invoice_ids = payload.get("invoice_ids", payload.get("selected_ids"))
        raw_status = payload.get("is_paid", payload.get("payment_status"))
    else:
        form = BulkInvoicePaymentForm()
        raw_invoice_ids = (
            request.form.getlist("invoice_ids")
            or request.form.get("invoice_ids")
        )
        raw_status = request.form.get("is_paid")

    normalized_invoice_ids = _normalize_invoice_ids(raw_invoice_ids)
    if normalized_invoice_ids:
        form.selected_ids.data = ",".join(normalized_invoice_ids)

    parsed_status = _parse_is_paid(raw_status)
    if parsed_status is True:
        form.payment_status.data = "paid"
    elif parsed_status is False:
        form.payment_status.data = "unpaid"
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
    is_paid = form.payment_status.data == "paid"

    updated_invoices, missing_invoice_ids = _apply_invoice_payment_status(
        invoice_ids, is_paid=is_paid
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

    status = "paid" if is_paid else "unpaid"
    if is_ajax:
        return {
            "success": True,
            "count": len(updated_invoices),
            "status": status,
            "updated": [
                {
                    "id": invoice.id,
                    "is_paid": invoice.is_paid,
                    "paid_at": (
                        invoice.paid_at.isoformat() if invoice.paid_at else None
                    ),
                    "payment_status": "Paid" if invoice.is_paid else "Unpaid",
                }
                for invoice in updated_invoices
            ],
        }

    flash(f"Marked {len(updated_invoices)} invoice(s) as {status}.", "success")
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
    payment_status = request.args.get("payment_status", "all").lower()
    if payment_status not in {"all", "paid", "unpaid"}:
        payment_status = "all"

    start_date = (
        datetime.fromisoformat(start_date_str) if start_date_str else None
    )
    end_date = datetime.fromisoformat(end_date_str) if end_date_str else None

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
    if payment_status == "paid":
        query = query.filter(Invoice.is_paid.is_(True))
    elif payment_status == "unpaid":
        query = query.filter(Invoice.is_paid.is_(False))

    invoices = query.order_by(Invoice.date_created.desc()).paginate(
        page=page, per_page=per_page
    )
    data = [
        {
            "id": inv.id,
            "date": inv.date_created.strftime("%Y-%m-%d"),
            "customer": f"{inv.customer.first_name} {inv.customer.last_name}",
            "payment_status": "Paid" if inv.is_paid else "Unpaid",
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
        invoice = _create_invoice_from_form(form)
        customer = invoice.customer
        return {
            "invoice": {
                "id": invoice.id,
                "date": invoice.date_created.strftime("%Y-%m-%d"),
                "customer": f"{customer.first_name} {customer.last_name}",
            }
        }
    return {"errors": form.errors}, 400


@invoice.route("/view_invoices", methods=["GET", "POST"])
@login_required
def view_invoices():
    """List invoices with optional filters."""
    form = InvoiceFilterForm()
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    form.customer_id.choices = [(-1, "All")] + [
        (c.id, f"{c.first_name} {c.last_name}")
        for c in Customer.query.order_by(Customer.last_name, Customer.first_name).all()
    ]

    user_id = request.args.get("user_id", type=int)

    # Determine filter values from form submission or query params
    if form.validate_on_submit():
        invoice_id = form.invoice_id.data
        customer_id = form.customer_id.data
        start_date = form.start_date.data
        end_date = form.end_date.data
        payment_status = request.form.get("payment_status", "all").lower()
    else:
        invoice_id = normalize_request_text_filter(request.args.get("invoice_id"))
        customer_id = request.args.get("customer_id", type=int)
        start_date_str = request.args.get("start_date")
        end_date_str = request.args.get("end_date")
        payment_status = request.args.get("payment_status", "all").lower()
        start_date = (
            datetime.fromisoformat(start_date_str) if start_date_str else None
        )
        end_date = (
            datetime.fromisoformat(end_date_str) if end_date_str else None
        )
        form.invoice_id.data = invoice_id
        if customer_id is not None:
            form.customer_id.data = customer_id
        if start_date:
            form.start_date.data = start_date
        if end_date:
            form.end_date.data = end_date
    if payment_status not in {"all", "paid", "unpaid"}:
        payment_status = "all"

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
    if payment_status == "paid":
        query = query.filter(Invoice.is_paid.is_(True))
    elif payment_status == "unpaid":
        query = query.filter(Invoice.is_paid.is_(False))
    invoices = query.order_by(Invoice.date_created.desc()).paginate(
        page=page, per_page=per_page
    )
    delete_form = DeleteForm()
    create_form = InvoiceForm()
    create_form.customer.choices = [
        (c.id, f"{c.first_name} {c.last_name}") for c in Customer.query.all()
    ]
    return render_template(
        "invoices/view_invoices.html",
        invoices=invoices,
        form=form,
        delete_form=delete_form,
        create_form=create_form,
        per_page=per_page,
        pagination_args=build_pagination_args(
            per_page,
            extra_params={
                "invoice_id": invoice_id or None,
                "user_id": user_id,
                "customer_id": customer_id,
                "start_date": start_date.isoformat() if start_date else None,
                "end_date": end_date.isoformat() if end_date else None,
                "payment_status": payment_status,
            },
        ),
    )
