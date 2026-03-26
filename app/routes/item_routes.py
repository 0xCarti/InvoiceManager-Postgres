import os

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from app import db
from app.forms import (
    BulkItemUpdateForm,
    CSRFOnlyForm,
    ImportItemsForm,
    ItemForm,
)
from app.models import (
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
    PurchaseOrderItem,
    Transfer,
    TransferItem,
    Vendor,
)
from app.utils.activity import log_activity
from app.utils.filter_state import (
    filters_to_query_args,
    get_filter_defaults,
    normalize_filters,
)
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.units import BASE_UNITS

item = Blueprint("item", __name__)

# Constants for the import_items route
# Only plain text files are allowed and uploads are capped at 1MB
ALLOWED_IMPORT_EXTENSIONS = {".txt"}
MAX_IMPORT_SIZE = 1 * 1024 * 1024  # 1 MB


@item.route("/items")
@login_required
def view_items():
    """Display the inventory item list."""
    session_key = "item_filters"
    scope = request.endpoint or "item.view_items"
    default_filters = get_filter_defaults(current_user, scope)

    if request.args.get("reset"):
        session.pop(session_key, None)
        if default_filters:
            session[session_key] = default_filters
            return redirect(
                url_for(
                    "item.view_items", **filters_to_query_args(default_filters)
                )
            )
        return redirect(url_for("item.view_items"))

    if not request.args:
        if session_key in session:
            stored_filters = normalize_filters(session[session_key])
            session[session_key] = stored_filters
            return redirect(
                url_for(
                    "item.view_items", **filters_to_query_args(stored_filters)
                )
            )
        if default_filters:
            session[session_key] = default_filters
            return redirect(
                url_for(
                    "item.view_items", **filters_to_query_args(default_filters)
                )
            )
    else:
        filters = normalize_filters(
            request.args, exclude=("page", "reset")
        )
        if filters:
            session[session_key] = filters
        else:
            session.pop(session_key, None)

    def _coerce_int_list(values):
        coerced = []
        for raw in values:
            try:
                coerced.append(int(raw))
            except (TypeError, ValueError):
                continue
        return coerced

    if request.args:
        filters = normalize_filters(request.args, exclude=("page", "reset"))
        purchase_filter_values = filters.get("purchase_gl_code_id", [])
        if purchase_filter_values:
            coerced_purchase_ids = _coerce_int_list(purchase_filter_values)
            filters["purchase_gl_code_id"] = [
                str(value) for value in coerced_purchase_ids
            ]
        session[session_key] = filters

    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    name_query = request.args.get("name_query", "")
    match_mode = request.args.get("match_mode", "contains")
    purchase_gl_code_params = request.args.getlist("purchase_gl_code_id")
    sales_gl_code_params = request.args.getlist("gl_code_id")
    purchase_gl_code_ids = _coerce_int_list(purchase_gl_code_params)
    sales_gl_code_ids = [int(x) for x in sales_gl_code_params if x.isdigit()]
    archived = request.args.get("archived", "active")
    base_unit = request.args.get("base_unit")
    cost_min = request.args.get("cost_min", type=float)
    cost_max = request.args.get("cost_max", type=float)
    vendor_ids = [
        int(x) for x in request.args.getlist("vendor_id") if x.isdigit()
    ]

    query = Item.query.options(
        selectinload(Item.units),
        selectinload(Item.purchase_gl_code),
        selectinload(Item.gl_code_rel),
    )
    if archived == "active":
        query = query.filter(Item.archived.is_(False))
    elif archived == "archived":
        query = query.filter(Item.archived.is_(True))
    if name_query:
        if match_mode == "exact":
            query = query.filter(Item.name == name_query)
        elif match_mode == "startswith":
            query = query.filter(Item.name.like(f"{name_query}%"))
        elif match_mode == "contains":
            query = query.filter(Item.name.like(f"%{name_query}%"))
        elif match_mode == "not_contains":
            query = query.filter(Item.name.notlike(f"%{name_query}%"))
        else:
            query = query.filter(Item.name.like(f"%{name_query}%"))

    if purchase_gl_code_ids:
        query = query.filter(Item.purchase_gl_code_id.in_(purchase_gl_code_ids))

    if sales_gl_code_ids:
        query = query.filter(Item.gl_code_id.in_(sales_gl_code_ids))

    if vendor_ids:
        query = (
            query.join(
                PurchaseOrderItem, PurchaseOrderItem.item_id == Item.id
            )
            .join(
                PurchaseOrder,
                PurchaseOrderItem.purchase_order_id == PurchaseOrder.id,
            )
            .filter(PurchaseOrder.vendor_id.in_(vendor_ids))
            .distinct()
        )
    if base_unit:
        query = query.filter(Item.base_unit == base_unit)
    if cost_min is not None and cost_max is not None and cost_min > cost_max:
        flash("Invalid cost range: min cannot be greater than max.", "error")
        session.pop(session_key, None)
        return redirect(url_for("item.view_items"))
    if cost_min is not None:
        query = query.filter(Item.cost >= cost_min)
    if cost_max is not None:
        query = query.filter(Item.cost <= cost_max)

    items = query.order_by(Item.name).paginate(
        page=page, per_page=per_page, error_out=False
    )
    if items.pages and page > items.pages:
        page = items.pages
        items = query.order_by(Item.name).paginate(
            page=page, per_page=per_page, error_out=False
        )

    item_last_received_map = {}
    page_item_ids = [item.id for item in items.items]
    if page_item_ids:
        results = (
            db.session.query(
                PurchaseInvoiceItem.item_id,
                func.max(PurchaseInvoice.received_date).label(
                    "last_purchase_received_date"
                ),
            )
            .join(
                PurchaseInvoice,
                PurchaseInvoiceItem.invoice_id == PurchaseInvoice.id,
            )
            .filter(PurchaseInvoiceItem.item_id.in_(page_item_ids))
            .group_by(PurchaseInvoiceItem.item_id)
            .all()
        )
        item_last_received_map = {
            item_id: last_received for item_id, last_received in results
        }

    for item in items.items:
        item.last_purchase_received_date = item_last_received_map.get(item.id)
    extra_pagination = {}
    if "archived" not in request.args:
        extra_pagination["archived"] = archived
    create_form = ItemForm()
    bulk_delete_form = CSRFOnlyForm()
    purchase_gl_codes = (
        GLCode.query.filter(
            or_(GLCode.code.like("5%"), GLCode.code.like("6%"))
        )
        .order_by(GLCode.code)
        .all()
    )
    base_units = [
        u
        for (u,) in db.session.query(Item.base_unit)
        .distinct()
        .order_by(Item.base_unit)
    ]
    vendors = Vendor.query.order_by(Vendor.first_name, Vendor.last_name).all()
    active_purchase_gl_codes = (
        GLCode.query.filter(GLCode.id.in_(purchase_gl_code_ids)).all()
        if purchase_gl_code_ids
        else []
    )
    active_sales_gl_codes = (
        GLCode.query.filter(GLCode.id.in_(sales_gl_code_ids)).all()
        if sales_gl_code_ids
        else []
    )
    active_gl_code_filter = None
    if active_purchase_gl_codes:
        active_gl_code_filter = {
            "label": "Purchase",
            "codes": active_purchase_gl_codes,
        }
    elif active_sales_gl_codes:
        active_gl_code_filter = {
            "label": "Inventory",
            "codes": active_sales_gl_codes,
        }
    active_vendors = (
        Vendor.query.filter(Vendor.id.in_(vendor_ids)).all() if vendor_ids else []
    )
    return render_template(
        "items/view_items.html",
        items=items,
        create_form=create_form,
        bulk_delete_form=bulk_delete_form,
        name_query=name_query,
        match_mode=match_mode,
        purchase_gl_codes=purchase_gl_codes,
        purchase_gl_code_ids=purchase_gl_code_ids,
        base_units=base_units,
        base_unit=base_unit,
        cost_min=cost_min,
        cost_max=cost_max,
        active_purchase_gl_codes=active_purchase_gl_codes,
        active_sales_gl_codes=active_sales_gl_codes,
        active_gl_code_filter=active_gl_code_filter,
        archived=archived,
        vendors=vendors,
        vendor_ids=vendor_ids,
        active_vendors=active_vendors,
        per_page=per_page,
        pagination_args=build_pagination_args(
            per_page, extra_params=extra_pagination
        ),
    )


def _parse_selected_ids(raw_value: str) -> list[int]:
    ids: list[int] = []
    for chunk in raw_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            raise ValueError("Invalid selection identifier.") from None
    return ids


def _render_item_bulk_form(form: BulkItemUpdateForm):
    return render_template("items/bulk_update_form.html", form=form)


@item.route("/items/bulk-update", methods=["GET", "POST"])
@login_required
def bulk_update_items():
    """Apply updates to multiple inventory items."""

    form = BulkItemUpdateForm()
    if request.method == "GET":
        raw_ids = request.args.getlist("ids") or request.args.getlist("id")
        try:
            selected_ids = [int(value) for value in raw_ids if int(value)]
        except ValueError:
            abort(400)
        if not selected_ids:
            abort(400)
        form.selected_ids.data = ",".join(str(value) for value in selected_ids)
        return _render_item_bulk_form(form)

    if form.validate_on_submit():
        try:
            selected_ids = _parse_selected_ids(form.selected_ids.data or "")
        except ValueError:
            form.selected_ids.errors.append("Unable to determine selected items.")
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {"success": False, "form_html": _render_item_bulk_form(form)}
                )
            flash("Unable to determine selected items for update.", "error")
            return redirect(url_for("item.view_items"))

        if not selected_ids:
            form.selected_ids.errors.append("Select at least one item to update.")
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {"success": False, "form_html": _render_item_bulk_form(form)}
                )
            flash("Select at least one item to update.", "error")
            return redirect(url_for("item.view_items"))

        query = (
            Item.query.options(
                selectinload(Item.units),
                selectinload(Item.purchase_gl_code),
                selectinload(Item.gl_code_rel),
            )
            .filter(Item.id.in_(selected_ids))
            .order_by(Item.id)
        )
        items = query.all()
        if len(items) != len(set(selected_ids)):
            form.selected_ids.errors.append(
                "Some selected items are no longer available."
            )
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {"success": False, "form_html": _render_item_bulk_form(form)}
                )
            flash("Some selected items are no longer available.", "error")
            return redirect(url_for("item.view_items"))

        apply_name = form.apply_name.data
        apply_base_unit = form.apply_base_unit.data
        apply_gl_code = form.apply_gl_code_id.data
        apply_purchase_gl = form.apply_purchase_gl_code_id.data
        apply_archived = form.apply_archived.data

        new_name = form.name.data if apply_name else None
        new_base_unit = form.base_unit.data if apply_base_unit else None
        new_gl_code_id = form.gl_code_id.data if apply_gl_code else None
        new_purchase_gl_code_id = (
            form.purchase_gl_code_id.data if apply_purchase_gl else None
        )
        new_archived = form.archived.data if apply_archived else None

        active_name_targets: dict[str, list[int]] = {}
        for item_obj in items:
            final_name = new_name if apply_name else item_obj.name
            final_archived = new_archived if apply_archived else item_obj.archived
            if final_name and not final_archived:
                active_name_targets.setdefault(final_name, []).append(item_obj.id)

        conflicting_names = {
            name for name, values in active_name_targets.items() if len(values) > 1
        }
        if conflicting_names:
            form.name.errors.append(
                "Cannot activate multiple items with the same name: "
                + ", ".join(sorted(conflicting_names))
            )
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {"success": False, "form_html": _render_item_bulk_form(form)}
                )
            flash(
                "Unable to activate multiple items with identical names.",
                "error",
            )
            return redirect(url_for("item.view_items"))

        if active_name_targets:
            existing_conflicts = (
                Item.query.filter(
                    Item.name.in_(active_name_targets.keys()),
                    Item.archived.is_(False),
                    ~Item.id.in_(selected_ids),
                )
                .with_entities(Item.name)
                .distinct()
                .all()
            )
            if existing_conflicts:
                names = ", ".join(sorted(name for (name,) in existing_conflicts))
                form.name.errors.append(
                    f"Active item already exists with name(s): {names}."
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify(
                        {
                            "success": False,
                            "form_html": _render_item_bulk_form(form),
                        }
                    )
                flash(
                    "Active items already exist with the requested names.",
                    "error",
                )
                return redirect(url_for("item.view_items"))

        if apply_base_unit:
            allowed_units = {value for value, _ in BASE_UNIT_CHOICES}
            if new_base_unit not in allowed_units:
                form.base_unit.errors.append("Select a valid base unit.")
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify(
                        {
                            "success": False,
                            "form_html": _render_item_bulk_form(form),
                        }
                    )
                flash("Select a valid base unit to apply.", "error")
                return redirect(url_for("item.view_items"))

        with db.session.begin_nested():
            for item_obj in items:
                if apply_name:
                    item_obj.name = new_name
                if apply_base_unit:
                    item_obj.base_unit = new_base_unit
                if apply_gl_code:
                    if new_gl_code_id:
                        item_obj.gl_code_id = new_gl_code_id or None
                        if item_obj.gl_code_id:
                            code = db.session.get(GLCode, item_obj.gl_code_id)
                            item_obj.gl_code = code.code if code else None
                        else:
                            item_obj.gl_code = None
                    else:
                        item_obj.gl_code_id = None
                        item_obj.gl_code = None
                if apply_purchase_gl:
                    item_obj.purchase_gl_code_id = (
                        new_purchase_gl_code_id or None
                    )
                if apply_archived:
                    item_obj.archived = new_archived
        db.session.commit()

        refreshed_items = (
            Item.query.options(
                selectinload(Item.units),
                selectinload(Item.purchase_gl_code),
                selectinload(Item.gl_code_rel),
            )
            .filter(Item.id.in_(selected_ids))
            .order_by(Item.id)
            .all()
        )

        log_activity(
            "Bulk updated items: " + ", ".join(str(item.id) for item in refreshed_items)
        )

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            rows = [
                {
                    "id": item_obj.id,
                    "html": render_template(
                        "items/_item_row.html", item=item_obj
                    ),
                }
                for item_obj in refreshed_items
            ]
            return jsonify({"success": True, "rows": rows})

        flash("Items updated successfully.", "success")
        return redirect(url_for("item.view_items"))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": False, "form_html": _render_item_bulk_form(form)})

    return _render_item_bulk_form(form)


@item.route("/items/recipe-cost-calculator")
@login_required
def recipe_cost_calculator():
    """Display an interactive tool to estimate recipe costs."""
    items = (
        Item.query.options(selectinload(Item.units))
        .filter(Item.archived.is_(False))
        .order_by(Item.name)
        .all()
    )
    items_payload = []
    for item in items:
        units = []
        seen = set()
        for unit in sorted(
            item.units, key=lambda u: (float(u.factor or 0.0), u.name.lower())
        ):
            key = (unit.name.lower(), round(float(unit.factor or 0.0), 6))
            if key in seen:
                continue
            seen.add(key)
            units.append(
                {
                    "id": unit.id,
                    "name": unit.name,
                    "factor": float(unit.factor or 1.0),
                    "is_base": unit.name == item.base_unit
                    or abs(float(unit.factor or 0.0) - 1.0) < 1e-6,
                }
            )
        if not any(u.get("is_base") for u in units):
            units.insert(
                0,
                {
                    "id": None,
                    "name": item.base_unit,
                    "factor": 1.0,
                    "is_base": True,
                },
            )
        items_payload.append(
            {
                "id": item.id,
                "name": item.name,
                "base_unit": item.base_unit,
                "cost": float(item.cost or 0.0),
                "units": units,
            }
        )
    return render_template("items/recipe_calculator.html", items=items_payload)


@item.route("/items/<int:item_id>")
@login_required
def view_item(item_id):
    """Display details for a single item."""
    item_obj = db.session.get(Item, item_id)
    if item_obj is None:
        abort(404)
    purchase_page = request.args.get("purchase_page", 1, type=int)
    sales_page = request.args.get("sales_page", 1, type=int)
    transfer_page = request.args.get("transfer_page", 1, type=int)
    purchase_per_page = get_per_page("purchase_per_page")
    sales_per_page = get_per_page("sales_per_page")
    transfer_per_page = get_per_page("transfer_per_page")
    purchase_items = (
        PurchaseInvoiceItem.query
        .join(PurchaseInvoice)
        .filter(PurchaseInvoiceItem.item_id == item_id)
        .order_by(PurchaseInvoice.received_date.desc(), PurchaseInvoice.id.desc())
        .paginate(
            page=purchase_page, per_page=purchase_per_page
        )
    )
    sales_items = (
        InvoiceProduct.query
        .join(Invoice, InvoiceProduct.invoice_id == Invoice.id)
        .join(Product, InvoiceProduct.product_id == Product.id, isouter=True)
        .join(ProductRecipeItem, ProductRecipeItem.product_id == Product.id)
        .filter(ProductRecipeItem.item_id == item_id)
        .order_by(Invoice.date_created.desc(), Invoice.id.desc())
        .paginate(
            page=sales_page, per_page=sales_per_page
        )
    )
    transfer_items = (
        TransferItem.query
        .join(Transfer)
        .filter(TransferItem.item_id == item_id)
        .order_by(Transfer.date_created.desc(), Transfer.id.desc())
        .paginate(
            page=transfer_page, per_page=transfer_per_page
        )
    )
    return render_template(
        "items/view_item.html",
        item=item_obj,
        purchase_items=purchase_items,
        sales_items=sales_items,
        transfer_items=transfer_items,
        purchase_per_page=purchase_per_page,
        sales_per_page=sales_per_page,
        transfer_per_page=transfer_per_page,
        purchase_pagination_args=build_pagination_args(
            purchase_per_page,
            page_param="purchase_page",
            per_page_param="purchase_per_page",
        ),
        sales_pagination_args=build_pagination_args(
            sales_per_page,
            page_param="sales_page",
            per_page_param="sales_per_page",
        ),
        transfer_pagination_args=build_pagination_args(
            transfer_per_page,
            page_param="transfer_page",
            per_page_param="transfer_per_page",
        ),
    )


@item.route("/items/<int:item_id>/locations", methods=["GET", "POST"])
@login_required
def item_locations(item_id):
    """Show all locations holding a specific item and their quantities."""
    item_obj = db.session.get(Item, item_id)
    if item_obj is None:
        abort(404)
    form = CSRFOnlyForm()
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()

    query = (
        LocationStandItem.query.join(Location)
        .options(
            selectinload(LocationStandItem.location),
            selectinload(LocationStandItem.purchase_gl_code),
        )
        .filter(LocationStandItem.item_id == item_id)
        .order_by(Location.name)
    )

    if form.validate_on_submit():
        updated = 0
        for record in query.paginate(page=page, per_page=per_page).items:
            field_name = f"location_gl_code_{record.location_id}"
            raw_value = request.form.get(field_name, "").strip()
            if raw_value:
                try:
                    new_value = int(raw_value)
                except ValueError:
                    continue
            else:
                new_value = None
            current_value = record.purchase_gl_code_id or None
            if new_value != current_value:
                record.purchase_gl_code_id = new_value
                updated += 1
        if updated:
            db.session.commit()
            flash("Location GL codes updated successfully.", "success")
        else:
            flash("No changes were made to location GL codes.", "info")
        return redirect(
            url_for(
                "item.item_locations",
                item_id=item_id,
                page=page,
                per_page=per_page,
            )
        )

    entries = query.paginate(page=page, per_page=per_page)
    total = (
        db.session.query(db.func.sum(LocationStandItem.expected_count))
        .filter_by(item_id=item_id)
        .scalar()
        or 0
    )
    return render_template(
        "items/item_locations.html",
        item=item_obj,
        entries=entries,
        total=total,
        per_page=per_page,
        form=form,
        purchase_gl_codes=ItemForm._fetch_purchase_gl_codes(),
        pagination_args=build_pagination_args(per_page),
    )


@item.route("/items/add", methods=["GET", "POST"])
@login_required
def add_item():
    """Add a new item to inventory."""
    form = ItemForm()
    if form.validate_on_submit():
        recv_count = sum(
            1
            for uf in form.units
            if uf.form.name.data and uf.form.receiving_default.data
        )
        trans_count = sum(
            1
            for uf in form.units
            if uf.form.name.data and uf.form.transfer_default.data
        )
        if recv_count > 1 or trans_count > 1:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                form_html = render_template("items/item_form.html", form=form)
                return jsonify({"success": False, "form_html": form_html})
            flash(
                "Only one unit can be set as receiving and transfer default.",
                "error",
            )
            return render_template(
                "items/item_form_page.html", form=form, title="Add Item"
            )
        item = Item(
            name=form.name.data,
            base_unit=form.base_unit.data,
            gl_code=form.gl_code.data if "gl_code" in request.form else None,
            gl_code_id=(
                form.gl_code_id.data if "gl_code_id" in request.form else None
            ),
            purchase_gl_code_id=form.purchase_gl_code.data or None,
        )
        db.session.add(item)
        db.session.commit()
        receiving_set = False
        transfer_set = False
        for uf in form.units:
            unit_form = uf.form
            if unit_form.name.data:
                receiving_default = (
                    unit_form.receiving_default.data and not receiving_set
                )
                transfer_default = (
                    unit_form.transfer_default.data and not transfer_set
                )
                db.session.add(
                    ItemUnit(
                        item_id=item.id,
                        name=unit_form.name.data,
                        factor=float(unit_form.factor.data),
                        receiving_default=receiving_default,
                        transfer_default=transfer_default,
                    )
                )
                if receiving_default:
                    receiving_set = True
                if transfer_default:
                    transfer_set = True
        db.session.commit()
        log_activity(f"Added item {item.name}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            row_html = render_template("items/_item_row.html", item=item)
            return jsonify({"success": True, "row_html": row_html, "item_id": item.id})
        flash("Item added successfully!")
        return redirect(url_for("item.view_items"))
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if request.method == "POST":
            form_html = render_template("items/item_form.html", form=form)
            return jsonify({"success": False, "form_html": form_html})
        return render_template("items/item_form.html", form=form)
    return render_template("items/item_form_page.html", form=form, title="Add Item")


@item.route("/items/copy/<int:item_id>")
@login_required
def copy_item(item_id):
    """Provide a pre-filled form for duplicating an item."""
    item = db.session.get(Item, item_id)
    if item is None:
        abort(404)
    form = ItemForm(obj=item)
    form.gl_code.data = item.gl_code
    form.gl_code_id.data = item.gl_code_id
    form.purchase_gl_code.data = item.purchase_gl_code_id
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("items/item_form.html", form=form)
    return render_template("items/item_form_page.html", form=form, title="Add Item")


@item.route("/items/edit/<int:item_id>", methods=["GET", "POST"])
@login_required
def edit_item(item_id):
    """Modify an existing item."""
    item = db.session.get(Item, item_id)
    if item is None:
        abort(404)
    location_stand_items = (
        LocationStandItem.query.join(Location)
        .options(
            selectinload(LocationStandItem.location),
            selectinload(LocationStandItem.purchase_gl_code),
        )
        .filter(LocationStandItem.item_id == item.id)
        .order_by(Location.name)
        .all()
    )
    recipe_product_items = (
        ProductRecipeItem.query.join(Product)
        .options(selectinload(ProductRecipeItem.product))
        .filter(ProductRecipeItem.item_id == item.id)
        .order_by(Product.name)
        .all()
    )
    purchase_gl_codes = ItemForm._fetch_purchase_gl_codes()
    form = ItemForm(obj=item)
    if request.method == "GET":
        form.gl_code.data = item.gl_code
        form.gl_code_id.data = item.gl_code_id
        form.purchase_gl_code.data = item.purchase_gl_code_id
    if form.validate_on_submit():
        recv_count = sum(
            1
            for uf in form.units
            if uf.form.name.data and uf.form.receiving_default.data
        )
        trans_count = sum(
            1
            for uf in form.units
            if uf.form.name.data and uf.form.transfer_default.data
        )
        if recv_count > 1 or trans_count > 1:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                form_html = render_template(
                    "items/item_form.html",
                    form=form,
                    item=item,
                    recipe_product_items=recipe_product_items,
                )
                return jsonify({"success": False, "form_html": form_html})

            flash(
                "Only one unit can be set as receiving and transfer default.",
                "error",
            )
            return render_template(
                "items/item_form_page.html",
                form=form,
                item=item,
                title="Edit Item",
                location_stand_items=location_stand_items,
                purchase_gl_codes=purchase_gl_codes,
                recipe_product_items=recipe_product_items,
            )
        item.name = form.name.data
        item.base_unit = form.base_unit.data
        if "gl_code" in request.form:
            item.gl_code = form.gl_code.data
        if "gl_code_id" in request.form:
            item.gl_code_id = form.gl_code_id.data
        item.purchase_gl_code_id = form.purchase_gl_code.data or None
        ItemUnit.query.filter_by(item_id=item.id).delete()
        receiving_set = False
        transfer_set = False
        for uf in form.units:
            unit_form = uf.form
            if unit_form.name.data:
                receiving_default = (
                    unit_form.receiving_default.data and not receiving_set
                )
                transfer_default = (
                    unit_form.transfer_default.data and not transfer_set
                )
                db.session.add(
                    ItemUnit(
                        item_id=item.id,
                        name=unit_form.name.data,
                        factor=float(unit_form.factor.data),
                        receiving_default=receiving_default,
                        transfer_default=transfer_default,
                    )
                )
                if receiving_default:
                    receiving_set = True
                if transfer_default:
                    transfer_set = True
        for record in location_stand_items:
            field_name = f"location_gl_code_{record.location_id}"
            raw_value = request.form.get(field_name, "").strip()
            if raw_value:
                try:
                    new_value = int(raw_value)
                except ValueError:
                    continue
            else:
                new_value = None
            record.purchase_gl_code_id = new_value
        db.session.commit()
        log_activity(f"Edited item {item.id}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            row_html = render_template("items/_item_row.html", item=item)
            return jsonify({"success": True, "row_html": row_html, "item_id": item.id})
        flash("Item updated successfully!")
        return redirect(url_for("item.view_items"))
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if request.method == "POST":
            form_html = render_template(
                "items/item_form.html",
                form=form,
                item=item,
                location_stand_items=location_stand_items,
                purchase_gl_codes=purchase_gl_codes,
                recipe_product_items=recipe_product_items,
            )
            return jsonify({"success": False, "form_html": form_html})
        return render_template(
            "items/item_form.html",
            form=form,
            item=item,
            location_stand_items=location_stand_items,
            purchase_gl_codes=purchase_gl_codes,
            recipe_product_items=recipe_product_items,
        )
    return render_template(
        "items/item_form_page.html",
        form=form,
        item=item,
        title="Edit Item",
        location_stand_items=location_stand_items,
        purchase_gl_codes=purchase_gl_codes,
        recipe_product_items=recipe_product_items,
    )


@item.route("/items/delete/<int:item_id>", methods=["POST"])
@login_required
def delete_item(item_id):
    """Delete an item from the catalog."""
    item = db.session.get(Item, item_id)
    if item is None:
        abort(404)
    item.archived = True
    db.session.commit()
    log_activity(f"Archived item {item.id}")
    flash("Item archived successfully!")
    return redirect(url_for("item.view_items"))


@item.route("/items/bulk_delete", methods=["POST"])
@login_required
def bulk_delete_items():
    """Delete multiple items in one request."""
    item_ids = request.form.getlist("item_ids")
    if item_ids:
        Item.query.filter(Item.id.in_(item_ids)).update(
            {"archived": True}, synchronize_session="fetch"
        )
        db.session.commit()
        log_activity(f'Bulk archived items {",".join(item_ids)}')
        flash("Selected items have been archived.", "success")
    else:
        flash("No items selected.", "warning")
    return redirect(url_for("item.view_items"))


@item.route("/items/search", methods=["GET"])
@login_required
def search_items():
    """Search items by name for autocomplete fields."""
    search_term = request.args.get("term", "")
    items = (
        Item.query.options(selectinload(Item.purchase_gl_code))
        .filter(Item.name.ilike(f"%{search_term}%"))
        .order_by(Item.name)
        .limit(20)
        .all()
    )
    items_data = [
        {
            "id": item.id,
            "name": item.name,
            "gl_code": item.purchase_gl_code.code if item.purchase_gl_code else "",
        }
        for item in items
    ]
    return jsonify(items_data)


@item.route("/items/quick_add", methods=["POST"])
@login_required
def quick_add_item():
    """Create a minimal item via AJAX for purchase orders."""
    data = request.get_json() or {}
    name = (data.get("name") or "").strip()
    base_unit = data.get("base_unit")
    purchase_gl_code = data.get("purchase_gl_code")
    try:
        purchase_gl_code = int(purchase_gl_code)
    except (TypeError, ValueError):
        purchase_gl_code = None
    raw_units = data.get("units")
    if not isinstance(raw_units, list):
        raw_units = []

    cleaned_units = []
    for unit in raw_units:
        if not isinstance(unit, dict):
            continue
        unit_name = (unit.get("name") or "").strip()
        try:
            unit_factor = float(unit.get("factor", 0))
        except (TypeError, ValueError):
            unit_factor = 0
        receiving_default = bool(unit.get("receiving_default"))
        transfer_default = bool(unit.get("transfer_default"))
        if not unit_name or unit_factor <= 0:
            continue
        if unit_name == base_unit:
            unit_factor = 1.0
        cleaned_units.append(
            {
                "name": unit_name,
                "factor": unit_factor,
                "receiving_default": receiving_default,
                "transfer_default": transfer_default,
            }
        )
    valid_units = set(BASE_UNITS)
    if (
        not name
        or base_unit not in valid_units
        or not purchase_gl_code
        or not cleaned_units
    ):
        return jsonify({"error": "Invalid data"}), 400
    if Item.query.filter_by(name=name, archived=False).first():
        return jsonify({"error": "Item exists"}), 400
    item = Item(
        name=name,
        base_unit=base_unit,
        purchase_gl_code_id=purchase_gl_code,
    )
    db.session.add(item)
    db.session.commit()
    units = {}
    receiving_set = False
    transfer_set = False

    def add_unit(name, factor, receiving=False, transfer=False):
        nonlocal receiving_set, transfer_set
        unit = units.get(name)
        receiving_flag = bool(receiving) and not receiving_set
        transfer_flag = bool(transfer) and not transfer_set
        if unit:
            if receiving_flag:
                unit.receiving_default = True
            if transfer_flag:
                unit.transfer_default = True
        else:
            units[name] = ItemUnit(
                item_id=item.id,
                name=name,
                factor=float(factor),
                receiving_default=receiving_flag,
                transfer_default=transfer_flag,
            )
        if receiving_flag:
            receiving_set = True
        if transfer_flag:
            transfer_set = True

    for unit in cleaned_units:
        add_unit(
            unit["name"],
            unit["factor"],
            receiving=unit["receiving_default"],
            transfer=unit["transfer_default"],
        )

    if base_unit not in units:
        add_unit(base_unit, 1.0)

    base_unit_entry = units.get(base_unit)
    if base_unit_entry:
        base_unit_entry.factor = 1.0

    if not receiving_set:
        add_unit(base_unit, 1.0, receiving=True)
    if not transfer_set:
        add_unit(base_unit, 1.0, transfer=True)

    db.session.add_all(units.values())
    db.session.commit()
    log_activity(f"Added item {item.name}")
    gl = db.session.get(GLCode, purchase_gl_code) if purchase_gl_code else None
    return jsonify({
        "id": item.id,
        "name": item.name,
        "gl_code": gl.code if gl else "",
    })


@item.route("/items/<int:item_id>/units", methods=["GET", "POST"])
@login_required
def item_units(item_id):
    """Return or update unit options for an item."""
    item = db.session.get(Item, item_id)
    if item is None:
        abort(404)

    location_id = request.args.get("location_id", type=int)

    def serialize_units() -> dict:
        if location_id:
            gl_obj = item.purchase_gl_code_for_location(location_id)
        else:
            gl_obj = item.purchase_gl_code

        sorted_units = sorted(
            item.units,
            key=lambda unit: (unit.name != item.base_unit, unit.name.lower()),
        )
        return {
            "base_unit": item.base_unit,
            "units": [
                {
                    "id": unit.id,
                    "name": unit.name,
                    "factor": unit.factor,
                    "receiving_default": unit.receiving_default,
                    "transfer_default": unit.transfer_default,
                }
                for unit in sorted_units
            ],
            "purchase_gl_code": {
                "id": gl_obj.id if gl_obj else None,
                "code": gl_obj.code if gl_obj else "",
                "description": gl_obj.description
                if gl_obj and gl_obj.description
                else "",
            },
        }

    if request.method == "GET":
        return jsonify(serialize_units())

    data = request.get_json() or {}
    raw_units = data.get("units")
    if not isinstance(raw_units, list):
        return jsonify({"error": "Invalid data"}), 400

    base_unit_name = item.base_unit
    cleaned_units = []
    base_entry = None
    seen_names = set()

    for entry in raw_units:
        if not isinstance(entry, dict):
            continue
        is_base = bool(entry.get("is_base"))
        try:
            unit_id = int(entry.get("id"))
        except (TypeError, ValueError):
            unit_id = None
        unit_name = (entry.get("name") or "").strip()
        try:
            factor = float(entry.get("factor", 0))
        except (TypeError, ValueError):
            factor = 0.0
        receiving_default = bool(entry.get("receiving_default"))
        transfer_default = bool(entry.get("transfer_default"))

        if is_base:
            unit_name = base_unit_name
            factor = 1.0

        if not unit_name or factor <= 0:
            continue

        if not is_base:
            key = unit_name.lower()
            if key in seen_names:
                continue
            seen_names.add(key)

        unit_data = {
            "id": unit_id,
            "name": unit_name,
            "factor": 1.0 if is_base else factor,
            "receiving_default": receiving_default,
            "transfer_default": transfer_default,
            "is_base": is_base,
        }

        if is_base and base_entry is None:
            base_entry = unit_data
        elif not is_base:
            cleaned_units.append(unit_data)

    if base_entry is None:
        existing_base = next(
            (unit for unit in item.units if unit.name == base_unit_name),
            None,
        )
        base_entry = {
            "id": existing_base.id if existing_base else None,
            "name": base_unit_name,
            "factor": 1.0,
            "receiving_default": False,
            "transfer_default": False,
            "is_base": True,
        }

    cleaned_units.insert(0, base_entry)

    receiving_assigned = False
    transfer_assigned = False
    for unit_data in cleaned_units:
        if unit_data["receiving_default"] and not receiving_assigned:
            receiving_assigned = True
        else:
            unit_data["receiving_default"] = False

        if unit_data["transfer_default"] and not transfer_assigned:
            transfer_assigned = True
        else:
            unit_data["transfer_default"] = False

    if not receiving_assigned and cleaned_units:
        cleaned_units[0]["receiving_default"] = True
        receiving_assigned = True

    if not transfer_assigned and cleaned_units:
        cleaned_units[0]["transfer_default"] = True
        transfer_assigned = True

    if not cleaned_units or not receiving_assigned or not transfer_assigned:
        return jsonify({"error": "Invalid data"}), 400

    existing_units = {unit.id: unit for unit in item.units}
    remaining_ids = set(existing_units.keys())

    for unit_data in cleaned_units:
        unit_id = unit_data.get("id")
        unit = existing_units.get(unit_id) if unit_id in existing_units else None
        if unit is None:
            unit = ItemUnit(item=item)
        else:
            remaining_ids.discard(unit_id)

        unit.name = unit_data["name"]
        unit.factor = unit_data["factor"]
        unit.receiving_default = unit_data["receiving_default"]
        unit.transfer_default = unit_data["transfer_default"]
        db.session.add(unit)

    for unit_id in remaining_ids:
        db.session.delete(existing_units[unit_id])

    db.session.flush()
    response_data = serialize_units()
    db.session.commit()
    log_activity(f"Updated units for item {item.id}")

    return jsonify(response_data)


@item.route("/items/<int:item_id>/last_cost")
@login_required
def item_last_cost(item_id):
    """Return the last recorded cost for an item."""
    unit_id = request.args.get("unit_id", type=int)
    item = db.session.get(Item, item_id)
    if item is None:
        abort(404)
    factor = 1.0
    if unit_id:
        unit = db.session.get(ItemUnit, unit_id)
        if unit:
            factor = unit.factor
    cost = (item.cost or 0.0) * factor
    deposit = (item.container_deposit or 0.0) * factor
    return jsonify({"cost": cost, "deposit": deposit})


@item.route("/import_items", methods=["GET", "POST"])
@login_required
def import_items():
    """Bulk import items from a text file."""
    form = ImportItemsForm()
    if form.validate_on_submit():
        from run import app

        file = form.file.data
        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if ext not in ALLOWED_IMPORT_EXTENSIONS:
            flash("Only .txt files are allowed.", "error")
            return redirect(url_for("item.import_items"))
        if size > MAX_IMPORT_SIZE:
            flash("File is too large.", "error")
            return redirect(url_for("item.import_items"))
        filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(filepath)

        # Read all unique item names from the uploaded file
        with open(filepath, "r") as file:
            names = {line.strip() for line in file if line.strip()}

        # Fetch existing active items in a single query and build a lookup
        existing_items = Item.query.filter(
            Item.name.in_(names), Item.archived.is_(False)
        ).all()
        existing_lookup = {item.name for item in existing_items}

        # Bulk create only the missing items
        new_items = [
            Item(name=name) for name in names if name not in existing_lookup
        ]
        if new_items:
            db.session.bulk_save_objects(new_items)
        db.session.commit()
        log_activity("Imported items from file")

        flash("Items imported successfully.", "success")
        return redirect(url_for("item.import_items"))

    return render_template("items/import_items.html", form=form)
