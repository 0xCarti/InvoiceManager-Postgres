from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required
from sqlalchemy.orm import selectinload

from app import db
from app.forms import (
    CSRFOnlyForm,
    MenuAssignmentForm,
    MenuForm,
    QuickProductForm,
)
from app.models import Location, Menu, MenuAssignment, Product
from app.utils.activity import log_activity
from app.utils.menu_assignments import set_location_menu, sync_menu_locations

menu = Blueprint("menu", __name__)


def _load_products(product_ids: list[int]) -> list[Product]:
    if not product_ids:
        return []
    unique_ids = list(dict.fromkeys(product_ids))
    products = Product.query.filter(Product.id.in_(unique_ids)).all()
    by_id = {product.id: product for product in products}
    return [by_id[pid] for pid in unique_ids if pid in by_id]


@menu.route("/menus")
@login_required
def view_menus():
    name_query = request.args.get("name_query", "").strip()
    match_mode = request.args.get("match_mode", "contains")
    assigned_status = request.args.get("assigned_status", "all")
    product_status = request.args.get("product_status", "all")

    query = Menu.query.options(
        selectinload(Menu.products),
        selectinload(Menu.assignments).selectinload(MenuAssignment.location),
    )

    if name_query:
        if match_mode == "exact":
            query = query.filter(Menu.name == name_query)
        elif match_mode == "startswith":
            query = query.filter(Menu.name.like(f"{name_query}%"))
        elif match_mode == "not_contains":
            query = query.filter(Menu.name.notlike(f"%{name_query}%"))
        else:  # default to contains
            match_mode = "contains"
            query = query.filter(Menu.name.like(f"%{name_query}%"))
    else:
        match_mode = "contains"

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

    if product_status == "with":
        query = query.filter(Menu.products.any())
    elif product_status == "without":
        query = query.filter(~Menu.products.any())
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
        menu = Menu(
            name=form.name.data,
            description=form.description.data,
        )
        menu.products = _load_products(form.product_ids.data)
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
        form.product_ids.data = [product.id for product in menu.products]
    if form.validate_on_submit():
        menu.name = form.name.data
        menu.description = form.description.data
        menu.products = _load_products(form.product_ids.data)
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
            "product_ids": [product.id for product in menu.products],
        }
    )
