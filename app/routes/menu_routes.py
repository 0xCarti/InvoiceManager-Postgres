from __future__ import annotations

import csv
import io
import secrets

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload

from app import db
from app.forms import (
    CSRFOnlyForm,
    MenuAssignmentForm,
    MenuForm,
    QuickProductForm,
)
from app.models import (
    Location,
    Menu,
    MenuAssignment,
    PlaylistItem,
    Product,
    ProductSellableAmount,
    Setting,
)
from app.utils.sellable_amounts import ensure_default_sellable_amount
from app.utils.activity import log_activity
from app.utils.menu_assignments import set_location_menu, sync_menu_locations
from app.utils.text import (
    normalize_request_text_filter,
    normalize_text_match_mode,
)

menu = Blueprint("menu", __name__)


def _load_products(product_ids: list[int]) -> list[Product]:
    if not product_ids:
        return []
    unique_ids = list(dict.fromkeys(product_ids))
    products = (
        Product.query.filter(
            Product.id.in_(unique_ids),
            Product.archived.is_(False),
        ).all()
    )
    by_id = {product.id: product for product in products}
    return [by_id[pid] for pid in unique_ids if pid in by_id]


def _load_sellable_amounts(amount_ids: list[int]) -> list[ProductSellableAmount]:
    if not amount_ids:
        return []
    unique_ids = list(dict.fromkeys(amount_ids))
    amounts = (
        ProductSellableAmount.query.options(selectinload(ProductSellableAmount.product))
        .join(Product)
        .filter(
            ProductSellableAmount.id.in_(unique_ids),
            ProductSellableAmount.active.is_(True),
            Product.archived.is_(False),
        )
        .all()
    )
    by_id = {amount.id: amount for amount in amounts}
    return [by_id[amount_id] for amount_id in unique_ids if amount_id in by_id]


def _products_for_sellable_amounts(
    amounts: list[ProductSellableAmount],
) -> list[Product]:
    products: list[Product] = []
    seen_ids: set[int] = set()
    for amount in amounts:
        product = amount.product
        if product is None or product.id in seen_ids:
            continue
        products.append(product)
        seen_ids.add(product.id)
    return products


def _resolve_menu_sellable_amounts(form: MenuForm) -> list[ProductSellableAmount]:
    amounts = _load_sellable_amounts(form.sellable_amount_ids.data or [])
    if amounts:
        return amounts

    # Compatibility for callers that still submit product_ids.
    products = _load_products(form.product_ids.data or [])
    if not products:
        return []
    return [ensure_default_sellable_amount(product) for product in products]


def _extract_menu_feed_token() -> str:
    token = (request.args.get("token") or "").strip()
    if token:
        return token
    token = (request.headers.get("X-API-Token") or "").strip()
    if token:
        return token
    authorization = (request.headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def _get_menu_feed_expected_token() -> str:
    token = str(current_app.config.get("MENU_FEED_API_TOKEN", "") or "").strip()
    if token:
        return token
    return Setting.get_menu_feed_api_token()


def _is_menu_feed_authorized() -> bool:
    expected_token = _get_menu_feed_expected_token()
    provided_token = _extract_menu_feed_token()
    if not expected_token or not provided_token:
        return False
    return secrets.compare_digest(provided_token, expected_token)


def _menu_feed_sellable_amounts() -> list[ProductSellableAmount]:
    return (
        ProductSellableAmount.query.options(selectinload(ProductSellableAmount.product))
        .join(Product)
        .filter(
            Product.archived.is_(False),
            ProductSellableAmount.active.is_(True),
        )
        .order_by(
            func.lower(Product.name),
            ProductSellableAmount.position,
            ProductSellableAmount.id,
        )
        .all()
    )


def _menu_feed_json_rows(
    amounts: list[ProductSellableAmount],
) -> list[dict[str, object]]:
    return [
        {
            "id": str(amount.id),
            "product_id": amount.product_id,
            "name": amount.display_name,
            "amount_name": amount.name,
            "quantity": float(amount.quantity or 1.0),
            "category": "",
            "image_url": "",
            "description": "",
            "enabled": 1,
            "price": round(amount.price_float, 2),
        }
        for amount in amounts
    ]


def _menu_feed_csv_text(rows: list[dict[str, object]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "product_id",
            "name",
            "amount_name",
            "quantity",
            "category",
            "image_url",
            "description",
            "enabled",
            "price",
        ],
        lineterminator="\n",
    )
    writer.writeheader()
    for row in rows:
        csv_row = dict(row)
        csv_row["price"] = f"{float(csv_row.get('price', 0.0)):.2f}"
        writer.writerow(csv_row)
    return output.getvalue()


def _resolve_menu_feed_format() -> str:
    requested_format = (request.args.get("format") or "").strip().lower()
    if requested_format in {"json", "csv"}:
        return requested_format
    if request.path.endswith(".csv"):
        return "csv"
    return "json"


@menu.route("/menus")
@login_required
def view_menus():
    name_query = normalize_request_text_filter(request.args.get("name_query"))
    match_mode = normalize_text_match_mode(request.args.get("match_mode"))
    assigned_status = request.args.get("assigned_status", "all")
    product_status = request.args.get("product_status", "all")

    query = Menu.query.options(
        selectinload(Menu.products),
        selectinload(Menu.sellable_amounts).selectinload(ProductSellableAmount.product),
        selectinload(Menu.assignments).selectinload(MenuAssignment.location),
    )

    if name_query:
        if match_mode == "exact":
            name_filter = func.lower(Menu.name) == name_query.lower()
        elif match_mode == "startswith":
            name_filter = Menu.name.ilike(f"{name_query}%")
        elif match_mode == "not_contains":
            name_filter = Menu.name.notilike(f"%{name_query}%")
        else:
            name_filter = Menu.name.ilike(f"%{name_query}%")
        query = query.filter(name_filter)

    if assigned_status == "assigned":
        query = query.filter(
            Menu.assignments.any(MenuAssignment.unassigned_at.is_(None))
        )
    elif assigned_status == "unassigned":
        query = query.filter(
            ~Menu.assignments.any(MenuAssignment.unassigned_at.is_(None))
        )
    else:
        assigned_status = "all"

    has_selection = or_(Menu.sellable_amounts.any(), Menu.products.any())
    if product_status == "with":
        query = query.filter(has_selection)
    elif product_status == "without":
        query = query.filter(~has_selection)
    else:
        product_status = "all"

    menus = query.order_by(Menu.name).all()
    delete_form = CSRFOnlyForm()
    return render_template(
        "menus/view_menus.html",
        menus=menus,
        delete_form=delete_form,
        name_query=name_query,
        match_mode=match_mode,
        assigned_status=assigned_status,
        product_status=product_status,
    )


@menu.route("/menus/add", methods=["GET", "POST"])
@login_required
def add_menu():
    form = MenuForm()
    quick_product_form = QuickProductForm()
    copy_menus = Menu.query.order_by(Menu.name).all()
    if form.validate_on_submit():
        sellable_amounts = _resolve_menu_sellable_amounts(form)
        menu = Menu(
            name=form.name.data,
            description=form.description.data,
        )
        menu.sellable_amounts = sellable_amounts
        menu.products = _products_for_sellable_amounts(sellable_amounts)
        db.session.add(menu)
        db.session.commit()
        log_activity(f"Created menu {menu.name}")
        flash("Menu created successfully.", "success")
        return redirect(url_for("menu.view_menus"))
    return render_template(
        "menus/edit_menu.html",
        form=form,
        menu=None,
        copy_menus=copy_menus,
        quick_product_form=quick_product_form,
    )


@menu.route("/menus/<int:menu_id>/edit", methods=["GET", "POST"])
@login_required
def edit_menu(menu_id: int):
    menu = db.session.get(Menu, menu_id)
    if menu is None:
        abort(404)
    form = MenuForm(obj=menu, obj_id=menu.id)
    quick_product_form = QuickProductForm()
    copy_menus = (
        Menu.query.filter(Menu.id != menu.id).order_by(Menu.name).all()
    )
    if request.method == "GET":
        form.sellable_amount_ids.data = [
            amount.id for amount in menu.active_sellable_amounts
        ]
        form.product_ids.data = [product.id for product in menu.products]
    if form.validate_on_submit():
        sellable_amounts = _resolve_menu_sellable_amounts(form)
        menu.name = form.name.data
        menu.description = form.description.data
        menu.sellable_amounts = sellable_amounts
        menu.products = _products_for_sellable_amounts(sellable_amounts)
        db.session.flush()
        sync_menu_locations(menu)
        db.session.commit()
        log_activity(f"Updated menu {menu.name}")
        flash("Menu updated successfully.", "success")
        return redirect(url_for("menu.view_menus"))
    return render_template(
        "menus/edit_menu.html",
        form=form,
        menu=menu,
        copy_menus=copy_menus,
        quick_product_form=quick_product_form,
    )


@menu.route("/menus/<int:menu_id>/delete", methods=["POST"])
@login_required
def delete_menu(menu_id: int):
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        flash("Unable to validate deletion request.", "danger")
        return redirect(url_for("menu.view_menus"))
    menu = db.session.get(Menu, menu_id)
    if menu is None:
        abort(404)
    if PlaylistItem.query.filter_by(menu_id=menu.id).first() is not None:
        flash("This menu is used by a signage playlist and cannot be deleted.", "danger")
        return redirect(url_for("menu.view_menus"))
    active_locations = [assignment.location for assignment in menu.assignments if assignment.unassigned_at is None and assignment.location]
    for location in active_locations:
        set_location_menu(location, None)
    db.session.delete(menu)
    db.session.commit()
    log_activity(f"Deleted menu {menu.name}")
    flash("Menu deleted successfully.", "success")
    return redirect(url_for("menu.view_menus"))


@menu.route("/menus/<int:menu_id>/assign", methods=["GET", "POST"])
@login_required
def assign_menu(menu_id: int):
    menu = db.session.get(Menu, menu_id)
    if menu is None:
        abort(404)
    form = MenuAssignmentForm()
    if request.method == "GET":
        form.location_ids.data = [loc.id for loc in Location.query.filter_by(current_menu_id=menu.id).all()]
    if form.validate_on_submit():
        selected_ids = set(form.location_ids.data)
        current_locations = Location.query.filter_by(current_menu_id=menu.id).all()
        for location in current_locations:
            if location.id not in selected_ids:
                set_location_menu(location, None)
        if selected_ids:
            locations = Location.query.filter(Location.id.in_(selected_ids)).all()
            for location in locations:
                set_location_menu(location, menu)
        db.session.commit()
        log_activity(
            "Updated menu assignments for {name}".format(name=menu.name)
        )
        flash("Menu assignments updated.", "success")
        return redirect(url_for("menu.view_menus"))
    return render_template("menus/assign_menu.html", form=form, menu=menu)


@menu.route("/menus/products")
@login_required
def get_menu_products():
    menu_id = request.args.get("menu_id", type=int)
    if menu_id is None:
        abort(400)
    menu = db.session.get(Menu, menu_id)
    if menu is None:
        abort(404)
    return jsonify(
        {
            "id": menu.id,
            "name": menu.name,
            "sellable_amount_ids": [
                amount.id for amount in menu.active_sellable_amounts
            ],
            "product_ids": [product.id for product in menu.products],
        }
    )


@menu.route("/integrations/menu-feed")
@menu.route("/integrations/menu-feed.json")
@menu.route("/integrations/menu-feed.csv")
def menu_feed():
    if not _is_menu_feed_authorized():
        abort(403)

    rows = _menu_feed_json_rows(_menu_feed_sellable_amounts())

    response_payload = {
        "count": len(rows),
        "products": rows,
    }

    if _resolve_menu_feed_format() == "csv":
        csv_text = _menu_feed_csv_text(rows)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": "inline; filename=menu-feed.csv"},
        )

    return jsonify(response_payload)
