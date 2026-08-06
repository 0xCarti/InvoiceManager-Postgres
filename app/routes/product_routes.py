from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

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
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, selectinload

from app import db
from app.forms import (
    BulkProductCostForm,
    BulkProductUpdateForm,
    DeleteForm,
    QuickProductForm,
    ProductRecipeForm,
    ProductWithRecipeForm,
)
from app.models import (
    GLCode,
    Item,
    ItemUnit,
    EventLocation,
    PosSalesImport,
    PosSalesImportRow,
    Product,
    ProductRecipeItem,
    ProductSellableAmount,
    Customer,
    Invoice,
    InvoiceProduct,
    Location,
    TerminalSale,
    TerminalSaleProductAlias,
    Vendor,
    VendorItemAlias,
)
from app.utils.activity import log_activity
from app.utils.filter_state import (
    filters_to_query_args,
    get_filter_defaults,
    normalize_filters,
)
from app.utils.numeric import coerce_float
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.menu_assignments import sync_menu_locations
from app.utils.recipe_history import backfill_invoice_product_recipe_snapshots
from app.utils.sellable_amounts import (
    normalize_sellable_amount_entries,
    replace_product_sellable_amounts,
    sync_product_legacy_prices,
    terminal_sale_unit_price,
    update_default_sellable_price,
)
from app.utils.text import (
    build_text_match_predicate,
    normalize_request_text_filter,
    normalize_text_match_mode,
)

product = Blueprint("product", __name__)


def _product_delete_blockers(product_id):
    """Return human-readable historical references that prevent product deletion."""
    blockers = []
    pos_row_count = (
        db.session.query(func.count(PosSalesImportRow.id))
        .filter(PosSalesImportRow.product_id == product_id)
        .scalar()
        or 0
    )
    if pos_row_count:
        blockers.append(
            f"{pos_row_count} POS sales import row"
            f"{'s' if pos_row_count != 1 else ''}"
        )

    terminal_sale_count = (
        db.session.query(func.count(TerminalSale.id))
        .filter(TerminalSale.product_id == product_id)
        .scalar()
        or 0
    )
    if terminal_sale_count:
        blockers.append(
            f"{terminal_sale_count} terminal sale"
            f"{'s' if terminal_sale_count != 1 else ''}"
        )

    invoice_line_count = (
        db.session.query(func.count(InvoiceProduct.id))
        .filter(InvoiceProduct.product_id == product_id)
        .scalar()
        or 0
    )
    if invoice_line_count:
        blockers.append(
            f"{invoice_line_count} sales invoice line"
            f"{'s' if invoice_line_count != 1 else ''}"
        )

    return blockers


def _sync_product_menu_locations(product_obj: Product) -> None:
    """Refresh assigned locations for menus that contain this product."""
    for menu in list(product_obj.menus):
        sync_menu_locations(menu)


def _sellable_entries_from_form(form) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for amount_form in getattr(form, "sellable_amounts", []):
        amount_fields = amount_form.form
        entries.append(
            {
                "amount_id": amount_fields.amount_id.data,
                "name": amount_fields.name.data,
                "quantity": amount_fields.quantity.data,
                "price": amount_fields.price.data,
                "active": amount_fields.active.data,
                "is_default": amount_fields.is_default.data,
            }
        )
    return normalize_sellable_amount_entries(
        entries,
        fallback_price=getattr(form, "price", None).data
        if getattr(form, "price", None) is not None
        else None,
    )


def _populate_sellable_amount_form(form, product_obj: Product | None = None) -> None:
    if product_obj is not None and product_obj.sellable_amounts:
        while len(form.sellable_amounts) < len(product_obj.sellable_amounts):
            form.sellable_amounts.append_entry()
        for index, amount in enumerate(product_obj.sellable_amounts):
            amount_form = form.sellable_amounts[index].form
            amount_form.amount_id.data = str(amount.id)
            amount_form.name.data = amount.name
            amount_form.quantity.data = amount.quantity
            amount_form.price.data = amount.price
            amount_form.active.data = bool(amount.active)
            amount_form.is_default.data = bool(amount.is_default)
        return

    if len(form.sellable_amounts) < 1:
        form.sellable_amounts.append_entry()
    amount_form = form.sellable_amounts[0].form
    if not amount_form.name.data:
        amount_form.name.data = "Each"
    if amount_form.quantity.data is None:
        amount_form.quantity.data = 1
    if amount_form.price.data is None and getattr(form, "price", None) is not None:
        amount_form.price.data = form.price.data
    amount_form.active.data = True
    amount_form.is_default.data = True


@product.route("/products")
@login_required
def view_products():
    """List available products."""
    session_key = "product_filters"
    scope = request.endpoint or "product.view_products"
    default_filters = get_filter_defaults(current_user, scope)

    if request.args.get("reset"):
        session.pop(session_key, None)
        if default_filters:
            session[session_key] = default_filters
            return redirect(
                url_for(
                    "product.view_products",
                    **filters_to_query_args(default_filters),
                )
            )
        return redirect(url_for("product.view_products"))

    if not request.args:
        if session_key in session:
            stored_filters = normalize_filters(session[session_key])
            session[session_key] = stored_filters
            return redirect(
                url_for(
                    "product.view_products",
                    **filters_to_query_args(stored_filters),
                )
            )
        if default_filters:
            session[session_key] = default_filters
            return redirect(
                url_for(
                    "product.view_products",
                    **filters_to_query_args(default_filters),
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

    delete_form = DeleteForm()
    bulk_cost_form = BulkProductCostForm()
    create_form = ProductWithRecipeForm()
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    name_query = normalize_request_text_filter(request.args.get("name_query"))
    match_mode = normalize_text_match_mode(request.args.get("match_mode"))
    sales_gl_code_ids = [
        int(x) for x in request.args.getlist("sales_gl_code_id") if x.isdigit()
    ]
    customer_id = request.args.get("customer_id", type=int)
    cost_min = coerce_float(request.args.get("cost_min"))
    cost_max = coerce_float(request.args.get("cost_max"))
    price_min = coerce_float(request.args.get("price_min"))
    price_max = coerce_float(request.args.get("price_max"))
    archived_filter = (request.args.get("archived") or "active").strip().lower()
    if archived_filter not in {"active", "archived", "all"}:
        archived_filter = "active"
    last_sold_before_str = request.args.get("last_sold_before")
    include_unsold = request.args.get("include_unsold") in [
        "1",
        "true",
        "True",
        "yes",
        "on",
    ]
    last_sold_before = None
    if last_sold_before_str:
        try:
            last_sold_before = datetime.strptime(last_sold_before_str, "%Y-%m-%d")
        except ValueError:
            flash(
                "Invalid date format for last_sold_before. Use YYYY-MM-DD.",
                "error",
            )
            return redirect(url_for("product.view_products"))

    query = Product.query
    if archived_filter == "active":
        query = query.filter(Product.archived.is_(False))
    elif archived_filter == "archived":
        query = query.filter(Product.archived.is_(True))
    if name_query:
        if match_mode == "exact":
            name_filter = func.lower(Product.name) == name_query.lower()
        elif match_mode == "startswith":
            name_filter = Product.name.ilike(f"{name_query}%")
        elif match_mode == "not_contains":
            name_filter = Product.name.notilike(f"%{name_query}%")
        else:
            name_filter = Product.name.ilike(f"%{name_query}%")
        query = query.filter(name_filter)

    if sales_gl_code_ids:
        query = query.filter(Product.sales_gl_code_id.in_(sales_gl_code_ids))
    if customer_id:
        invoice_alias = aliased(Invoice)
        query = (
            query.join(InvoiceProduct, Product.invoice_products)
            .join(invoice_alias, InvoiceProduct.invoice_id == invoice_alias.id)
            .filter(invoice_alias.customer_id == customer_id)
            .distinct()
        )

    if cost_min is not None and cost_max is not None and cost_min > cost_max:
        flash("Invalid cost range: min cannot be greater than max.", "error")
        session.pop(session_key, None)
        return redirect(url_for("product.view_products"))
    if (
        price_min is not None
        and price_max is not None
        and price_min > price_max
    ):
        flash("Invalid price range: min cannot be greater than max.", "error")
        session.pop(session_key, None)
        return redirect(url_for("product.view_products"))
    if cost_min is not None:
        query = query.filter(Product.cost >= cost_min)
    if cost_max is not None:
        query = query.filter(Product.cost <= cost_max)
    if price_min is not None:
        query = query.filter(Product.price >= price_min)
    if price_max is not None:
        query = query.filter(Product.price <= price_max)
    invoice_last_sold = func.max(Invoice.date_created)
    terminal_sale_last_sold = func.max(TerminalSale.sold_at)
    greatest_last_sold = func.greatest(
        invoice_last_sold, terminal_sale_last_sold
    )
    last_sold_expr = func.coalesce(
        greatest_last_sold, invoice_last_sold, terminal_sale_last_sold
    )
    query = (
        query.outerjoin(InvoiceProduct, Product.invoice_products)
        .outerjoin(Invoice, InvoiceProduct.invoice_id == Invoice.id)
        .outerjoin(TerminalSale, Product.id == TerminalSale.product_id)
        .group_by(Product.id)
    )
    if last_sold_before:
        if include_unsold:
            query = query.having(
                or_(
                    last_sold_expr < last_sold_before,
                    last_sold_expr.is_(None),
                )
            )
        else:
            query = query.having(last_sold_expr < last_sold_before)

    query = query.options(
        selectinload(Product.sales_gl_code),
        selectinload(Product.locations),
        selectinload(Product.menus),
        selectinload(Product.sellable_amounts),
        selectinload(Product.recipe_items).selectinload(ProductRecipeItem.item),
        selectinload(Product.recipe_items).selectinload(ProductRecipeItem.unit),
    )

    products = query.paginate(page=page, per_page=per_page)
    sales_gl_codes = (
        GLCode.query.filter(GLCode.code.like("4%")).order_by(GLCode.code).all()
    )
    selected_sales_gl_codes = (
        GLCode.query.filter(GLCode.id.in_(sales_gl_code_ids)).all()
        if sales_gl_code_ids
        else []
    )
    customers = Customer.query.order_by(Customer.last_name, Customer.first_name).all()
    selected_customer = Customer.query.get(customer_id) if customer_id else None
    return render_template(
        "products/view_products.html",
        products=products,
        delete_form=delete_form,
        form=create_form,
        name_query=name_query,
        match_mode=match_mode,
        sales_gl_code_ids=sales_gl_code_ids,
        sales_gl_codes=sales_gl_codes,
        selected_sales_gl_codes=selected_sales_gl_codes,
        customer_id=customer_id,
        customers=customers,
        selected_customer=selected_customer,
        cost_min=cost_min,
        cost_max=cost_max,
        price_min=price_min,
        price_max=price_max,
        archived_filter=archived_filter,
        last_sold_before=last_sold_before_str,
        include_unsold=include_unsold,
        bulk_cost_form=bulk_cost_form,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


def _parse_product_ids(raw_value: str) -> list[int]:
    ids: list[int] = []
    for chunk in raw_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            raise ValueError("Invalid product identifier.") from None
    return ids


def _render_product_bulk_form(form: BulkProductUpdateForm):
    return render_template("products/bulk_update_form.html", form=form)


def _safe_local_return_url(value: str | None) -> str | None:
    candidate = (value or "").strip().replace("\\", "")
    if not candidate:
        return None
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc or not candidate.startswith("/"):
        return None
    return candidate


def _get_sales_import_product_create_context():
    sales_import_id = request.args.get("sales_import_id", type=int)
    import_row_id = request.args.get("import_row_id", type=int)
    return_location_id = request.args.get("return_location_id", type=int)
    location_filter = (request.args.get("location_filter") or "").strip().lower()
    if location_filter not in {"all", "issues"}:
        location_filter = "all"

    if sales_import_id is None and import_row_id is None and return_location_id is None:
        return None

    if not current_user.has_permission("sales_imports.manage"):
        abort(403)
    if sales_import_id is None or import_row_id is None:
        abort(400)

    import_record = (
        PosSalesImport.query.options(selectinload(PosSalesImport.rows))
        .filter(PosSalesImport.id == sales_import_id)
        .first()
    )
    if import_record is None:
        abort(404)

    row_record = next(
        (row for row in import_record.rows if row.id == import_row_id),
        None,
    )
    if row_record is None:
        abort(404)

    if return_location_id is None:
        return_location_id = row_record.location_import_id

    return {
        "sales_import_id": import_record.id,
        "import_record": import_record,
        "row_record": row_record,
        "return_location_id": return_location_id,
        "return_url": url_for(
            "admin.sales_import_detail",
            import_id=import_record.id,
            location_id=return_location_id,
            location_filter=location_filter,
        ),
        "form_action": url_for(
            "product.create_product",
            sales_import_id=import_record.id,
            import_row_id=row_record.id,
            return_location_id=return_location_id,
            location_filter=location_filter,
        ),
    }


def _map_product_to_sales_import(import_record, row_record, product_id: int) -> None:
    normalized_key = row_record.normalized_product_name
    for scoped_row in import_record.rows:
        if scoped_row.normalized_product_name == normalized_key:
            scoped_row.product_id = product_id

    alias = TerminalSaleProductAlias.query.filter_by(
        normalized_name=normalized_key
    ).first()
    if alias is None:
        alias = TerminalSaleProductAlias(
            source_name=row_record.source_product_name,
            normalized_name=normalized_key,
            product_id=product_id,
        )
        db.session.add(alias)
    else:
        alias.source_name = row_record.source_product_name
        alias.product_id = product_id


def _build_product_vendor_alias_groups(product_obj: Product) -> list[dict[str, object]]:
    item_order: list[int] = []
    items_by_id: dict[int, Item] = {}
    for recipe_item in product_obj.recipe_items:
        if recipe_item.item_id is None or recipe_item.item is None:
            continue
        if recipe_item.item_id in items_by_id:
            continue
        item_order.append(recipe_item.item_id)
        items_by_id[recipe_item.item_id] = recipe_item.item

    aliases_by_item_id: dict[int, list[VendorItemAlias]] = {}
    if item_order:
        vendor_aliases = (
            VendorItemAlias.query.options(
                selectinload(VendorItemAlias.vendor),
                selectinload(VendorItemAlias.item_unit),
            )
            .join(Vendor, Vendor.id == VendorItemAlias.vendor_id)
            .filter(VendorItemAlias.item_id.in_(item_order))
            .order_by(
                VendorItemAlias.item_id,
                Vendor.first_name,
                Vendor.last_name,
                VendorItemAlias.vendor_sku,
                VendorItemAlias.vendor_description,
            )
            .all()
        )
        for alias in vendor_aliases:
            aliases_by_item_id.setdefault(alias.item_id, []).append(alias)

    return [
        {
            "item": items_by_id[item_id],
            "aliases": aliases_by_item_id.get(item_id, []),
        }
        for item_id in item_order
    ]


def _normalize_recipe_yield_quantity(value) -> float:
    yield_quantity = coerce_float(value)
    if yield_quantity is None or yield_quantity <= 0:
        return 1.0
    return float(yield_quantity)


def _build_recipe_entries_from_item_forms(item_forms) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for item_form in item_forms:
        item_id = item_form.item.data
        quantity = coerce_float(item_form.quantity.data)
        if not item_id or quantity is None:
            continue
        entries.append(
            {
                "item_id": item_id,
                "unit_id": item_form.unit.data or None,
                "quantity": quantity,
                "countable": bool(item_form.countable.data),
            }
        )
    return entries


def _build_recipe_entries_from_product(
    product_obj: Product,
) -> list[dict[str, object]]:
    return [
        {
            "item_id": recipe_item.item_id,
            "unit_id": recipe_item.unit_id,
            "quantity": recipe_item.quantity,
            "countable": recipe_item.countable,
        }
        for recipe_item in product_obj.recipe_items
        if recipe_item.item_id is not None and recipe_item.quantity is not None
    ]


def _calculate_recipe_cost_from_entries(
    recipe_entries: list[dict[str, object]],
    yield_quantity,
) -> float:
    batch_cost = 0.0
    for entry in recipe_entries:
        item = db.session.get(Item, entry.get("item_id"))
        if item is None:
            continue
        quantity = coerce_float(entry.get("quantity"))
        if quantity is None:
            continue
        factor = 1.0
        unit_id = entry.get("unit_id")
        if unit_id:
            unit = db.session.get(ItemUnit, unit_id)
            if unit and (unit.item_id == item.id or unit.item_id is None):
                factor = coerce_float(unit.factor) or 1.0
        batch_cost += (item.cost or 0.0) * quantity * factor

    normalized_yield_quantity = _normalize_recipe_yield_quantity(yield_quantity)
    return batch_cost / normalized_yield_quantity


def _replace_product_recipe_items(
    product_obj: Product,
    recipe_entries: list[dict[str, object]],
) -> None:
    if product_obj.id is not None:
        backfill_invoice_product_recipe_snapshots(product_obj)
    ProductRecipeItem.query.filter_by(product_id=product_obj.id).delete()
    for entry in recipe_entries:
        db.session.add(
            ProductRecipeItem(
                product_id=product_obj.id,
                item_id=entry["item_id"],
                unit_id=entry["unit_id"],
                quantity=entry["quantity"],
                countable=entry["countable"],
            )
        )


def _sync_auto_recipe_cost(product_obj: Product) -> bool:
    if not product_obj.auto_update_recipe_cost:
        return False
    recalculated_cost = _calculate_recipe_cost_from_entries(
        _build_recipe_entries_from_product(product_obj),
        product_obj.recipe_yield_quantity,
    )
    previous_cost = coerce_float(product_obj.cost) or 0.0
    if abs(previous_cost - recalculated_cost) < 1e-9:
        return False
    product_obj.cost = recalculated_cost
    return True


@product.route("/products/bulk-update", methods=["GET", "POST"])
@login_required
def bulk_update_products():
    """Apply updates to multiple products."""

    form = BulkProductUpdateForm()
    if request.method == "GET":
        raw_ids = request.args.getlist("ids") or request.args.getlist("id")
        try:
            selected_ids = [int(value) for value in raw_ids if int(value)]
        except ValueError:
            abort(400)
        if not selected_ids:
            abort(400)
        form.selected_ids.data = ",".join(str(value) for value in selected_ids)
        return _render_product_bulk_form(form)

    if form.validate_on_submit():
        try:
            selected_ids = _parse_product_ids(form.selected_ids.data or "")
        except ValueError:
            form.selected_ids.errors.append("Unable to determine selected products.")
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {
                        "success": False,
                        "form_html": _render_product_bulk_form(form),
                    }
                )
            flash("Unable to determine selected products for update.", "error")
            return redirect(url_for("product.view_products"))

        if not selected_ids:
            form.selected_ids.errors.append(
                "Select at least one product to update."
            )
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {
                        "success": False,
                        "form_html": _render_product_bulk_form(form),
                    }
                )
            flash("Select at least one product to update.", "error")
            return redirect(url_for("product.view_products"))

        query = (
            Product.query.options(
                selectinload(Product.sales_gl_code),
                selectinload(Product.locations),
                selectinload(Product.menus),
                selectinload(Product.sellable_amounts),
                selectinload(Product.recipe_items).selectinload(
                    ProductRecipeItem.item
                ),
                selectinload(Product.recipe_items).selectinload(
                    ProductRecipeItem.unit
                ),
            )
            .filter(Product.id.in_(selected_ids))
            .order_by(Product.id)
        )
        products = query.all()
        if len(products) != len(set(selected_ids)):
            form.selected_ids.errors.append(
                "Some selected products are no longer available."
            )
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {
                        "success": False,
                        "form_html": _render_product_bulk_form(form),
                    }
                )
            flash("Some selected products are no longer available.", "error")
            return redirect(url_for("product.view_products"))

        apply_name = form.apply_name.data
        apply_price = form.apply_price.data
        apply_cost = form.apply_cost.data
        apply_sales_gl = form.apply_sales_gl_code_id.data

        new_name = form.name.data if apply_name else None
        new_price = float(form.price.data) if apply_price else None
        new_cost = float(form.cost.data) if apply_cost else None
        new_sales_gl = form.sales_gl_code_id.data if apply_sales_gl else None

        if apply_name and new_name:
            if len(selected_ids) > 1:
                form.name.errors.append(
                    "Cannot assign the same name to multiple products."
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify(
                        {
                            "success": False,
                            "form_html": _render_product_bulk_form(form),
                        }
                    )
                flash(
                    "Cannot assign the same name to multiple products.",
                    "error",
                )
                return redirect(url_for("product.view_products"))
            conflict = (
                Product.query.filter(Product.name == new_name)
                .filter(~Product.id.in_(selected_ids))
                .first()
            )
            if conflict:
                form.name.errors.append(
                    "A product with that name already exists."
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify(
                        {
                            "success": False,
                            "form_html": _render_product_bulk_form(form),
                        }
                    )
                flash("A product with that name already exists.", "error")
                return redirect(url_for("product.view_products"))

        with db.session.begin_nested():
            for product_obj in products:
                if apply_name:
                    product_obj.name = new_name
                if apply_price:
                    update_default_sellable_price(product_obj, new_price)
                if apply_cost:
                    product_obj.cost = new_cost if new_cost is not None else 0.0
                if apply_sales_gl:
                    product_obj.sales_gl_code_id = new_sales_gl or None
        db.session.commit()

        refreshed_products = (
            Product.query.options(
                selectinload(Product.sales_gl_code),
                selectinload(Product.locations),
                selectinload(Product.menus),
                selectinload(Product.sellable_amounts),
                selectinload(Product.recipe_items).selectinload(
                    ProductRecipeItem.item
                ),
                selectinload(Product.recipe_items).selectinload(
                    ProductRecipeItem.unit
                ),
            )
            .filter(Product.id.in_(selected_ids))
            .order_by(Product.id)
            .all()
        )

        log_activity(
            "Bulk updated products: "
            + ", ".join(str(prod.id) for prod in refreshed_products)
        )

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            delete_form = DeleteForm()
            rows = [
                {
                    "id": product_obj.id,
                    "html": render_template(
                        "products/_product_row.html",
                        product=product_obj,
                        delete_form=delete_form,
                    ),
                }
                for product_obj in refreshed_products
            ]
            return jsonify({"success": True, "rows": rows})

        flash("Products updated successfully.", "success")
        return redirect(url_for("product.view_products"))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(
            {"success": False, "form_html": _render_product_bulk_form(form)}
        )

    return _render_product_bulk_form(form)


@product.route("/products/create", methods=["GET", "POST"])
@login_required
def create_product():
    """Add a new product definition."""
    sales_import_context = _get_sales_import_product_create_context()
    form = ProductWithRecipeForm()
    if request.method == "GET" and sales_import_context is not None:
        row_record = sales_import_context["row_record"]
        if not form.name.data:
            form.name.data = row_record.source_product_name
        if form.price.data is None:
            imported_unit_price = coerce_float(row_record.computed_unit_price)
            if imported_unit_price is not None:
                try:
                    form.price.data = Decimal(str(imported_unit_price))
                except (InvalidOperation, ValueError):
                    form.price.data = None
        _populate_sellable_amount_form(form)

    if form.validate_on_submit():
        yield_quantity = _normalize_recipe_yield_quantity(
            form.recipe_yield_quantity.data
        )
        sellable_entries = _sellable_entries_from_form(form)
        default_entry = next(
            (entry for entry in sellable_entries if entry.get("is_default")),
            sellable_entries[0],
        )
        default_price = default_entry.get("price", 0.0)
        recipe_entries = _build_recipe_entries_from_item_forms(form.items)
        auto_update_recipe_cost = bool(form.auto_update_recipe_cost.data)
        sales_gl_code_id = form.sales_gl_code.data
        if not sales_gl_code_id:
            sales_gl_code_id = None

        product = Product(
            name=form.name.data,
            price=default_price,
            invoice_sale_price=default_price,
            cost=(
                _calculate_recipe_cost_from_entries(recipe_entries, yield_quantity)
                if auto_update_recipe_cost
                else coerce_float(form.cost.data) or 0.0
            ),
            auto_update_recipe_cost=auto_update_recipe_cost,
            sales_gl_code_id=sales_gl_code_id,
            recipe_yield_quantity=yield_quantity,
            recipe_yield_unit=form.recipe_yield_unit.data or None,
        )
        db.session.add(product)
        db.session.flush()

        replace_product_sellable_amounts(product, sellable_entries)
        _replace_product_recipe_items(product, recipe_entries)
        if sales_import_context is not None:
            _map_product_to_sales_import(
                sales_import_context["import_record"],
                sales_import_context["row_record"],
                product.id,
            )

        db.session.commit()
        log_activity(f"Created product {product.name}")
        if sales_import_context is not None:
            log_activity(
                f"Mapped POS sales import {sales_import_context['sales_import_id']} "
                f"product '{sales_import_context['row_record'].source_product_name}' "
                f"to created product {product.id}"
            )
            flash("Product created and mapped back to the sales import.", "success")
            return redirect(sales_import_context["return_url"])

        flash("Product created successfully!", "success")
        return redirect(url_for("product.view_products"))
    if form.recipe_yield_quantity.data is None:
        form.recipe_yield_quantity.data = 1
    if request.method == "GET" and sales_import_context is None:
        _populate_sellable_amount_form(form)
    return render_template(
        "products/create_product.html",
        form=form,
        product_id=None,
        form_action=(
            sales_import_context["form_action"]
            if sales_import_context is not None
            else url_for("product.create_product")
        ),
        title=(
            "Create Product for Sales Import"
            if sales_import_context is not None
            else "Create Product"
        ),
        return_to_sales_import_url=(
            sales_import_context["return_url"]
            if sales_import_context is not None
            else None
        ),
        sales_import_context=sales_import_context,
    )


@product.route("/products/ajax/create", methods=["POST"])
@login_required
def ajax_create_product():
    """Create a product via AJAX."""
    form = ProductWithRecipeForm()
    if form.validate_on_submit():
        yield_quantity = _normalize_recipe_yield_quantity(
            form.recipe_yield_quantity.data
        )
        sellable_entries = _sellable_entries_from_form(form)
        default_entry = next(
            (entry for entry in sellable_entries if entry.get("is_default")),
            sellable_entries[0],
        )
        default_price = default_entry.get("price", 0.0)
        recipe_entries = _build_recipe_entries_from_item_forms(form.items)
        auto_update_recipe_cost = bool(form.auto_update_recipe_cost.data)
        sales_gl_code_id = form.sales_gl_code.data
        if not sales_gl_code_id:
            sales_gl_code_id = None

        product = Product(
            name=form.name.data,
            price=default_price,
            invoice_sale_price=default_price,
            cost=(
                _calculate_recipe_cost_from_entries(recipe_entries, yield_quantity)
                if auto_update_recipe_cost
                else coerce_float(form.cost.data) or 0.0
            ),
            auto_update_recipe_cost=auto_update_recipe_cost,
            sales_gl_code_id=sales_gl_code_id,
            recipe_yield_quantity=yield_quantity,
            recipe_yield_unit=form.recipe_yield_unit.data or None,
        )
        db.session.add(product)
        db.session.flush()
        replace_product_sellable_amounts(product, sellable_entries)
        _replace_product_recipe_items(product, recipe_entries)
        db.session.commit()
        log_activity(f"Created product {product.name}")
        row_html = render_template(
            "products/_product_row.html", product=product, delete_form=DeleteForm()
        )
        product_payload = {
            "id": product.id,
            "name": product.name,
            "price": product.default_sellable_price,
            "sellable_amounts": [
                {
                    "id": amount.id,
                    "name": amount.name,
                    "price": amount.price_float,
                    "quantity": amount.quantity,
                }
                for amount in product.active_sellable_amounts
            ],
        }
        return jsonify(success=True, html=row_html, product=product_payload)
    return jsonify(success=False, errors=form.errors), 400


@product.route("/products/quick-create", methods=["POST"])
@login_required
def quick_create_product():
    """Create a lightweight product record for use in menus."""

    form = QuickProductForm()
    if form.validate_on_submit():
        cost = form.cost.data if form.cost.data is not None else 0.0
        sales_gl_code_id = form.sales_gl_code.data or None
        if sales_gl_code_id == 0:
            sales_gl_code_id = None

        yield_quantity = form.recipe_yield_quantity.data
        if yield_quantity is None or yield_quantity <= 0:
            yield_quantity = 1

        sellable_entries = _sellable_entries_from_form(form)
        default_entry = next(
            (entry for entry in sellable_entries if entry.get("is_default")),
            sellable_entries[0],
        )
        default_price = default_entry.get("price", 0.0)
        product_obj = Product(
            name=form.name.data,
            price=default_price,
            invoice_sale_price=default_price,
            cost=cost,
            sales_gl_code_id=sales_gl_code_id,
            recipe_yield_quantity=float(yield_quantity),
            recipe_yield_unit=form.recipe_yield_unit.data or None,
        )
        db.session.add(product_obj)
        db.session.flush()
        replace_product_sellable_amounts(product_obj, sellable_entries)

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

        db.session.commit()
        log_activity(f"Quick created product {product_obj.name}")
        return (
            jsonify(
                success=True,
                product={
                    "id": product_obj.id,
                    "name": product_obj.name,
                    "sellable_amounts": [
                        {
                            "id": amount.id,
                            "name": amount.name,
                            "price": amount.price_float,
                            "quantity": amount.quantity,
                        }
                        for amount in product_obj.active_sellable_amounts
                    ],
                },
            ),
            201,
        )
    return jsonify(success=False, errors=form.errors), 400


@product.route("/products/ajax/validate", methods=["POST"])
@login_required
def validate_product_form():
    """Validate product form via AJAX without saving."""
    form = ProductWithRecipeForm()
    if form.validate_on_submit():
        return jsonify(success=True)
    return jsonify(success=False, errors=form.errors), 400


@product.route("/products/copy/<int:product_id>")
@login_required
def copy_product(product_id):
    """Provide a pre-filled form for duplicating a product."""
    product_obj = db.session.get(Product, product_id)
    if product_obj is None:
        abort(404)
    form = ProductWithRecipeForm()
    current_cost = (
        _calculate_recipe_cost_from_entries(
            _build_recipe_entries_from_product(product_obj),
            product_obj.recipe_yield_quantity,
        )
        if product_obj.auto_update_recipe_cost
        else (product_obj.cost or 0.0)
    )
    form.name.data = product_obj.name
    form.archived.data = False
    form.price.data = product_obj.price
    form.invoice_sale_price.data = product_obj.invoice_sale_price
    _populate_sellable_amount_form(form, product_obj)
    for amount_form in form.sellable_amounts:
        amount_form.form.amount_id.data = ""
    form.cost.data = current_cost
    form.auto_update_recipe_cost.data = product_obj.auto_update_recipe_cost
    form.sales_gl_code.data = product_obj.sales_gl_code_id
    form.recipe_yield_quantity.data = product_obj.recipe_yield_quantity or 1.0
    form.recipe_yield_unit.data = product_obj.recipe_yield_unit
    form.items.min_entries = len(product_obj.recipe_items)
    item_choices = [
        (itm.id, itm.name) for itm in Item.query.filter_by(archived=False).all()
    ]
    unit_choices = [(u.id, u.name) for u in ItemUnit.query.all()]
    for i, recipe_item in enumerate(product_obj.recipe_items):
        if len(form.items) <= i:
            form.items.append_entry()
        form.items[i].item.choices = item_choices
        form.items[i].unit.choices = unit_choices
        form.items[i].item.data = recipe_item.item_id
        form.items[i].unit.data = recipe_item.unit_id
        form.items[i].quantity.data = recipe_item.quantity
        form.items[i].countable.data = recipe_item.countable
    for i in range(len(product_obj.recipe_items), len(form.items)):
        form.items[i].item.choices = item_choices
        form.items[i].unit.choices = unit_choices
    return render_template(
        "products/create_product.html", form=form, product_id=None, title="Copy Product"
    )


@product.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def edit_product(product_id):
    """Edit product details and recipe."""
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    form = ProductWithRecipeForm()
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if form.validate_on_submit():
        was_archived = bool(product.archived)
        product.name = form.name.data
        product.archived = bool(form.archived.data)
        sellable_entries = _sellable_entries_from_form(form)
        recipe_entries = _build_recipe_entries_from_item_forms(form.items)
        product.auto_update_recipe_cost = bool(form.auto_update_recipe_cost.data)
        product.cost = (
            _calculate_recipe_cost_from_entries(
                recipe_entries,
                form.recipe_yield_quantity.data,
            )
            if product.auto_update_recipe_cost
            else coerce_float(form.cost.data) or 0.0
        )
        sales_gl_code_id = form.sales_gl_code.data
        if not sales_gl_code_id:
            sales_gl_code_id = None

        product.sales_gl_code_id = sales_gl_code_id
        product.recipe_yield_quantity = _normalize_recipe_yield_quantity(
            form.recipe_yield_quantity.data
        )
        product.recipe_yield_unit = form.recipe_yield_unit.data or None
        replace_product_sellable_amounts(product, sellable_entries)
        _replace_product_recipe_items(product, recipe_entries)
        if was_archived != bool(product.archived):
            _sync_product_menu_locations(product)
        db.session.commit()
        log_activity(f"Edited product {product.id}")
        if not is_ajax:
            flash("Product updated successfully!", "success")
            return redirect(url_for("product.view_products"))
        row_html = render_template(
            "products/_product_row.html",
            product=product,
            delete_form=DeleteForm(),
        )
        return jsonify(success=True, product_id=product.id, row_html=row_html)
    elif request.method == "GET":
        auto_cost_changed = _sync_auto_recipe_cost(product)
        if auto_cost_changed:
            db.session.commit()
            log_activity(f"Auto-updated recipe cost for product {product.id}")
        form.name.data = product.name
        form.archived.data = bool(product.archived)
        form.price.data = product.price
        form.invoice_sale_price.data = product.invoice_sale_price
        _populate_sellable_amount_form(form, product)
        form.cost.data = product.cost or 0.0
        form.auto_update_recipe_cost.data = product.auto_update_recipe_cost
        form.sales_gl_code.data = product.sales_gl_code_id
        form.recipe_yield_quantity.data = product.recipe_yield_quantity or 1.0
        form.recipe_yield_unit.data = product.recipe_yield_unit
        form.items.min_entries = len(product.recipe_items)
        item_choices = [
            (itm.id, itm.name)
            for itm in Item.query.filter_by(archived=False).all()
        ]
        unit_choices = [(u.id, u.name) for u in ItemUnit.query.all()]
        for i, recipe_item in enumerate(product.recipe_items):
            if len(form.items) <= i:
                form.items.append_entry()
                form.items[i].item.choices = item_choices
                form.items[i].unit.choices = unit_choices
            else:
                form.items[i].item.choices = item_choices
                form.items[i].unit.choices = unit_choices
            form.items[i].item.data = recipe_item.item_id
            form.items[i].unit.data = recipe_item.unit_id
            form.items[i].quantity.data = recipe_item.quantity
            form.items[i].countable.data = recipe_item.countable
    else:
        print(form.errors)
        print(form.cost.data)
    can_view_vendor_aliases = current_user.can_access_endpoint(
        "admin.vendor_item_aliases", "GET"
    )
    vendor_alias_groups = (
        _build_product_vendor_alias_groups(product) if can_view_vendor_aliases else []
    )
    form_action = url_for("product.edit_product", product_id=product.id)
    if is_ajax:
        modal_html = render_template(
            "products/_edit_product_tabs.html",
            form=form,
            product_id=product.id,
            vendor_alias_groups=vendor_alias_groups,
            terminal_sale_aliases=product.terminal_sale_aliases,
            alias_delete_form=DeleteForm(),
            form_action=form_action,
            form_id="edit-product-form",
            tabs_id="productEditTabsModal",
            tabs_content_id="productEditTabsModalContent",
        )
        if request.method == "POST":
            return jsonify(success=False, form_html=modal_html), 400
        return modal_html
    return render_template(
        "products/edit_product.html",
        form=form,
        product_id=product.id,
        vendor_alias_groups=vendor_alias_groups,
        terminal_sale_aliases=product.terminal_sale_aliases,
        alias_delete_form=DeleteForm(),
    )


@product.route("/products/<int:product_id>")
@login_required
def view_product(product_id: int):
    """Display details for a single product."""
    product_obj = (
        Product.query.options(
            selectinload(Product.sales_gl_code),
            selectinload(Product.locations).selectinload(Location.current_menu),
            selectinload(Product.menus),
            selectinload(Product.sellable_amounts),
            selectinload(Product.recipe_items).selectinload(ProductRecipeItem.item),
            selectinload(Product.recipe_items).selectinload(ProductRecipeItem.unit),
            selectinload(Product.terminal_sale_aliases),
        )
        .filter(Product.id == product_id)
        .first()
    )
    if product_obj is None:
        abort(404)

    can_view_vendor_aliases = current_user.can_access_endpoint(
        "admin.vendor_item_aliases", "GET"
    )
    vendor_alias_groups = (
        _build_product_vendor_alias_groups(product_obj)
        if can_view_vendor_aliases
        else []
    )
    alias_delete_form = DeleteForm()

    sales_page = request.args.get("sales_page", 1, type=int)
    terminal_sales_page = request.args.get("terminal_sales_page", 1, type=int)
    sales_per_page = get_per_page("sales_per_page")
    terminal_sales_per_page = get_per_page("terminal_sales_per_page")

    sales_items = (
        InvoiceProduct.query.options(
            selectinload(InvoiceProduct.invoice),
            selectinload(InvoiceProduct.product),
        )
        .join(Invoice, InvoiceProduct.invoice_id == Invoice.id)
        .filter(InvoiceProduct.product_id == product_id)
        .order_by(Invoice.date_created.desc(), Invoice.id.desc())
        .paginate(page=sales_page, per_page=sales_per_page)
    )
    terminal_sales = (
        TerminalSale.query.options(
            selectinload(TerminalSale.event_location).selectinload(
                EventLocation.event
            ),
            selectinload(TerminalSale.event_location).selectinload(
                EventLocation.location
            ),
            selectinload(TerminalSale.sellable_amount),
            selectinload(TerminalSale.pos_sales_import),
        )
        .filter(TerminalSale.product_id == product_id)
        .order_by(TerminalSale.sold_at.desc(), TerminalSale.id.desc())
        .paginate(page=terminal_sales_page, per_page=terminal_sales_per_page)
    )
    for sale in terminal_sales.items:
        sale.unit_price = terminal_sale_unit_price(sale)

    latest_invoice_sale = (
        db.session.query(func.max(Invoice.date_created))
        .join(InvoiceProduct, InvoiceProduct.invoice_id == Invoice.id)
        .filter(InvoiceProduct.product_id == product_id)
        .scalar()
    )
    latest_terminal_sale = (
        db.session.query(func.max(TerminalSale.sold_at))
        .filter(TerminalSale.product_id == product_id)
        .scalar()
    )
    last_sold_at = max(
        [value for value in [latest_invoice_sale, latest_terminal_sale] if value],
        default=None,
    )

    return render_template(
        "products/view_product.html",
        product=product_obj,
        can_view_vendor_aliases=can_view_vendor_aliases,
        vendor_alias_groups=vendor_alias_groups,
        alias_delete_form=alias_delete_form,
        sales_items=sales_items,
        terminal_sales=terminal_sales,
        sales_per_page=sales_per_page,
        terminal_sales_per_page=terminal_sales_per_page,
        sales_pagination_args=build_pagination_args(
            sales_per_page,
            page_param="sales_page",
            per_page_param="sales_per_page",
        ),
        terminal_sales_pagination_args=build_pagination_args(
            terminal_sales_per_page,
            page_param="terminal_sales_page",
            per_page_param="terminal_sales_per_page",
        ),
        last_sold_at=last_sold_at,
    )


@product.route(
    "/products/<int:product_id>/terminal_sale_aliases/<int:alias_id>/delete",
    methods=["POST"],
)
@login_required
def remove_terminal_sale_alias(product_id: int, alias_id: int):
    """Remove a terminal sale alias mapping from a product."""
    redirect_target = _safe_local_return_url(request.args.get("next")) or url_for(
        "product.edit_product", product_id=product_id, _anchor="terminal-sales"
    )

    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    alias = TerminalSaleProductAlias.query.filter_by(
        id=alias_id, product_id=product.id
    ).first()
    if alias is None:
        abort(404)

    form = DeleteForm()
    if form.validate_on_submit():
        db.session.delete(alias)
        db.session.commit()
        log_activity(
            f"Removed terminal sale alias {alias.id} from product {product.id}"
        )
        flash("Terminal sales product mapping removed.", "success")
    else:
        flash("Unable to remove mapping. Please try again.", "danger")

    return redirect(redirect_target)


@product.route("/products/<int:product_id>/recipe", methods=["GET", "POST"])
@login_required
def edit_product_recipe(product_id):
    """Edit the recipe for a product."""
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    form = ProductRecipeForm()
    if form.validate_on_submit():
        yield_quantity = _normalize_recipe_yield_quantity(
            form.recipe_yield_quantity.data
        )
        recipe_entries: list[dict[str, object]] = []
        previous_cost = coerce_float(product.cost) or 0.0
        product.recipe_yield_quantity = yield_quantity
        product.recipe_yield_unit = form.recipe_yield_unit.data or None
        ProductRecipeItem.query.filter_by(product_id=product.id).delete()
        items = [
            key
            for key in request.form.keys()
            if key.startswith("items-") and key.endswith("-item")
        ]
        for field in items:
            index = field.split("-")[1]
            item_id = request.form.get(f"items-{index}-item", type=int)
            unit_id = request.form.get(f"items-{index}-unit", type=int)
            quantity = coerce_float(request.form.get(f"items-{index}-quantity"))
            countable = request.form.get(f"items-{index}-countable") == "y"
            if item_id and quantity is not None:
                recipe_entries.append(
                    {
                        "item_id": item_id,
                        "unit_id": unit_id or None,
                        "quantity": quantity,
                        "countable": countable,
                    }
                )
                db.session.add(
                    ProductRecipeItem(
                        product_id=product.id,
                        item_id=item_id,
                        unit_id=unit_id,
                        quantity=quantity,
                        countable=countable,
                    )
                )
        if product.auto_update_recipe_cost:
            product.cost = _calculate_recipe_cost_from_entries(
                recipe_entries,
                product.recipe_yield_quantity,
            )
        db.session.commit()
        if (
            product.auto_update_recipe_cost
            and abs(previous_cost - (product.cost or 0.0)) >= 1e-9
        ):
            log_activity(
                f"Edited recipe and auto-updated cost for product {product.id}"
            )
        else:
            log_activity(f"Edited recipe for product {product.id}")
        flash("Recipe updated successfully!", "success")
        return redirect(url_for("product.view_products"))
    elif request.method == "GET":
        form.recipe_yield_quantity.data = product.recipe_yield_quantity or 1.0
        form.recipe_yield_unit.data = product.recipe_yield_unit
        form.items.min_entries = max(1, len(product.recipe_items))
        item_choices = [
            (itm.id, itm.name)
            for itm in Item.query.filter_by(archived=False).all()
        ]
        unit_choices = [(u.id, u.name) for u in ItemUnit.query.all()]
        for i, recipe_item in enumerate(product.recipe_items):
            if len(form.items) <= i:
                form.items.append_entry()
                form.items[i].item.choices = item_choices
                form.items[i].unit.choices = unit_choices
            else:
                form.items[i].item.choices = item_choices
                form.items[i].unit.choices = unit_choices
            form.items[i].item.data = recipe_item.item_id
            form.items[i].unit.data = recipe_item.unit_id
            form.items[i].quantity.data = recipe_item.quantity
            form.items[i].countable.data = recipe_item.countable
    return render_template(
        "products/edit_product_recipe.html", form=form, product=product
    )


@product.route("/products/<int:product_id>/calculate_cost")
@login_required
def calculate_product_cost(product_id):
    """Calculate the total recipe cost for a product."""
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    yield_quantity = request.args.get("yield_quantity")
    if coerce_float(yield_quantity) is None:
        yield_quantity = product.recipe_yield_quantity
    normalized_yield_quantity = _normalize_recipe_yield_quantity(yield_quantity)
    per_unit_cost = _calculate_recipe_cost_from_entries(
        _build_recipe_entries_from_product(product),
        normalized_yield_quantity,
    )
    return jsonify(
        {
            "cost": per_unit_cost,
            "batch_cost": per_unit_cost * normalized_yield_quantity,
            "yield_quantity": normalized_yield_quantity,
            "yield_unit": product.recipe_yield_unit,
        }
    )


@product.route("/products/calculate_cost_preview", methods=["POST"])
@login_required
def calculate_product_cost_preview():
    """Calculate recipe cost from the posted form data."""
    payload = request.get_json(silent=True) or {}
    normalized_yield_quantity = _normalize_recipe_yield_quantity(
        payload.get("yield_quantity")
    )
    per_unit_cost = _calculate_recipe_cost_from_entries(
        payload.get("items") or [],
        normalized_yield_quantity,
    )
    yield_unit = payload.get("yield_unit")

    return jsonify(
        {
            "cost": per_unit_cost,
            "batch_cost": per_unit_cost * normalized_yield_quantity,
            "yield_quantity": normalized_yield_quantity,
            "yield_unit": yield_unit,
        }
    )


@product.route("/products/bulk_set_cost_from_recipe", methods=["POST"])
@login_required
def bulk_set_cost_from_recipe():
    """Recalculate cost from recipe for selected products."""
    form = BulkProductCostForm()
    if not form.validate_on_submit():
        flash("Invalid request.", "error")
        return redirect(url_for("product.view_products"))

    raw_ids = request.form.getlist("product_ids")
    product_ids = []
    for raw_id in raw_ids:
        try:
            product_ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    product_ids = list(dict.fromkeys(product_ids))
    if not product_ids:
        flash("No products selected for recipe cost update.", "warning")
        return redirect(url_for("product.view_products"))

    query = Product.query.options(
        selectinload(Product.recipe_items).selectinload(ProductRecipeItem.item),
        selectinload(Product.recipe_items).selectinload(ProductRecipeItem.unit),
    ).filter(Product.id.in_(product_ids))

    products = query.all()
    if not products:
        flash("No products selected for recipe cost update.", "warning")
        return redirect(url_for("product.view_products"))

    updated = 0
    for product_obj in products:
        product_obj.cost = _calculate_recipe_cost_from_entries(
            _build_recipe_entries_from_product(product_obj),
            product_obj.recipe_yield_quantity,
        )
        updated += 1

    db.session.commit()
    log_activity(
        f"Bulk updated product cost from recipe for {updated} product"
        f"{'s' if updated != 1 else ''}"
    )
    flash(
        f"Updated recipe cost for {updated} product{'s' if updated != 1 else ''}.",
        "success",
    )
    return redirect(url_for("product.view_products"))


@product.route("/products/bulk_archive", methods=["POST"])
@login_required
def bulk_archive_products():
    """Archive selected products from current operational use."""
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)

    product_ids = []
    for raw_id in request.form.getlist("product_ids"):
        try:
            product_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if product_id > 0:
            product_ids.append(product_id)

    product_ids = list(dict.fromkeys(product_ids))
    if not product_ids:
        flash("No products selected for archive.", "warning")
        return redirect(url_for("product.view_products"))

    products = (
        Product.query.options(selectinload(Product.menus))
        .filter(Product.id.in_(product_ids))
        .order_by(Product.id)
        .all()
    )
    if not products:
        flash("No matching products were found to archive.", "warning")
        return redirect(url_for("product.view_products"))

    archived_count = 0
    already_archived_count = 0
    for product_obj in products:
        if product_obj.archived:
            already_archived_count += 1
            continue
        product_obj.archived = True
        _sync_product_menu_locations(product_obj)
        archived_count += 1

    if archived_count:
        db.session.commit()
        log_activity(
            "Bulk archived products: "
            + ", ".join(str(product_obj.id) for product_obj in products)
        )
        flash(
            f"Archived {archived_count} product"
            f"{'s' if archived_count != 1 else ''}. Historical sales and "
            "reports will still show archived products.",
            "success",
        )
    else:
        flash("Selected products were already archived.", "info")

    if already_archived_count and archived_count:
        flash(
            f"{already_archived_count} selected product"
            f"{'s were' if already_archived_count != 1 else ' was'} already archived.",
            "info",
        )

    return redirect(url_for("product.view_products"))


@product.route("/products/<int:product_id>/delete", methods=["POST"])
@login_required
def delete_product(product_id):
    """Delete a product and its recipe."""
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    blockers = _product_delete_blockers(product.id)
    if blockers:
        if product.archived:
            flash(
                "Product is already archived. Historical sales and reporting "
                "data remain available.",
                "info",
            )
            return redirect(url_for("product.view_products"))
        product.archived = True
        _sync_product_menu_locations(product)
        db.session.commit()
        log_activity(
            f"Archived product {product.id} because it is used by {', '.join(blockers)}"
        )
        flash(
            "Product was archived because it is used by "
            f"{', '.join(blockers)}. It is hidden from current product pickers, "
            "but historical sales and reports will still show it.",
            "success",
        )
        return redirect(url_for("product.view_products"))

    try:
        db.session.delete(product)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        product = db.session.get(Product, product_id)
        if product is not None and not product.archived:
            product.archived = True
            _sync_product_menu_locations(product)
            db.session.commit()
            log_activity(
                f"Archived product {product.id} after delete was blocked by historical references"
            )
            flash(
                "Product was archived because it is still referenced by "
                "historical records. It is hidden from current product pickers, "
                "but historical sales and reports will still show it.",
                "success",
            )
            return redirect(url_for("product.view_products"))
        flash(
            "Product cannot be deleted because it is still referenced by "
            "historical records. Remove current links where possible, or leave "
            "the product in place for audit history.",
            "warning",
        )
        return redirect(url_for("product.view_products"))

    log_activity(f"Deleted product {product.id}")
    flash("Product deleted successfully!", "success")
    return redirect(url_for("product.view_products"))


@product.route("/search_products")
@login_required
def search_products():
    """Return products matching a search query."""
    query = normalize_request_text_filter(request.args.get("query"))
    if not query:
        return jsonify([])
    matched_products = (
        Product.query.filter(
            Product.archived.is_(False),
            build_text_match_predicate(Product.name, query, "contains")
        )
        .options(selectinload(Product.sellable_amounts))
        .order_by(Product.name)
        .limit(25)
        .all()
    )
    product_data = []
    for product_obj in matched_products:
        amounts = product_obj.active_sellable_amounts
        if not amounts:
            sync_product_legacy_prices(product_obj)
            db.session.flush()
            amounts = product_obj.active_sellable_amounts
        product_data.append(
            {
                "id": product_obj.id,
                "name": product_obj.name,
                "price": product_obj.default_sellable_price,
                "sellable_amounts": [
                    {
                        "id": amount.id,
                        "name": amount.name,
                        "display_name": amount.display_name,
                        "quantity": amount.quantity,
                        "price": amount.price_float,
                        "is_default": amount.is_default,
                    }
                    for amount in amounts
                ],
            }
        )
    return jsonify(product_data)
