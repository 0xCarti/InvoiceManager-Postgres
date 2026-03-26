from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from urllib.parse import urlsplit
from flask_login import login_required

from app import db
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app.forms import (
    BulkLocationUpdateForm,
    CSRFOnlyForm,
    DeleteForm,
    ItemForm,
    LocationForm,
    LocationItemAddForm,
)
from app.models import GLCode, Item, Location, LocationStandItem, Menu
from app.services.pdf import render_stand_sheet_pdf
from app.utils.activity import log_activity
from app.utils.menu_assignments import apply_menu_products, set_location_menu
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.units import (
    DEFAULT_BASE_UNIT_CONVERSIONS,
    convert_quantity_for_reporting,
    get_unit_label,
)
from app.utils.text import normalize_name_for_sorting
from app.utils.email import SMTPConfigurationError, send_email

location = Blueprint("locations", __name__)


def _build_location_stand_sheet_items(location: Location):
    configured = current_app.config.get("BASE_UNIT_CONVERSIONS") or {}
    conversions = dict(DEFAULT_BASE_UNIT_CONVERSIONS)
    conversions.update(configured)

    stand_records = LocationStandItem.query.filter_by(
        location_id=location.id
    ).all()
    stand_by_item_id = {record.item_id: record for record in stand_records}

    stand_items = []
    seen = set()
    for product_obj in location.products:
        for recipe_item in product_obj.recipe_items:
            if recipe_item.countable and recipe_item.item_id not in seen:
                seen.add(recipe_item.item_id)
                record = stand_by_item_id.get(recipe_item.item_id)
                expected = record.expected_count if record else 0
                item_obj = recipe_item.item
                if item_obj.base_unit:
                    display_expected, report_unit = convert_quantity_for_reporting(
                        float(expected), item_obj.base_unit, conversions
                    )
                else:
                    display_expected, report_unit = expected, item_obj.base_unit
                stand_items.append(
                    {
                        "item": item_obj,
                        "expected": display_expected,
                        "report_unit_label": get_unit_label(report_unit),
                    }
                )

    stand_items.sort(
        key=lambda entry: normalize_name_for_sorting(
            entry["item"].name
        ).casefold()
    )
    return stand_items


def _protected_location_item_ids(location_obj: Location) -> set[int]:
    """Return item ids that cannot be removed from the location."""

    protected = set()
    for product_obj in location_obj.products:
        for recipe_item in product_obj.recipe_items:
            if recipe_item.countable:
                protected.add(recipe_item.item_id)
    return protected


def _location_items_redirect(location_id: int, page: str | None, per_page: str | None):
    """Redirect back to the location items view preserving pagination."""

    args = {"location_id": location_id}
    if page and page.isdigit():
        args["page"] = int(page)
    if per_page and per_page.isdigit():
        args["per_page"] = int(per_page)
    return redirect(url_for("locations.location_items", **args))


def _safe_next_url(raw_value: str | None) -> str | None:
    """Return a safe relative URL derived from ``raw_value``."""

    if not raw_value:
        return None

    parsed = urlsplit(raw_value)
    if parsed.scheme or parsed.netloc:
        return None

    sanitized = parsed.geturl()
    if sanitized.endswith("?"):
        sanitized = sanitized[:-1]
    if not sanitized.startswith("/"):
        sanitized = f"/{sanitized}"
    return sanitized
@location.route("/locations/add", methods=["GET", "POST"])
@login_required
def add_location():
    """Create a new location."""
    form = LocationForm()
    if form.validate_on_submit():
        menu_obj = None
        menu_id = form.menu_id.data or 0
        if menu_id:
            menu_obj = db.session.get(Menu, menu_id)
            if menu_obj is None:
                form.menu_id.errors.append("Selected menu is no longer available.")
                return render_template("locations/add_location.html", form=form)
        new_location = Location(
            name=form.name.data, is_spoilage=form.is_spoilage.data
        )
        db.session.add(new_location)
        db.session.flush()
        if menu_obj is not None:
            set_location_menu(new_location, menu_obj)
        else:
            apply_menu_products(new_location, None)
        db.session.commit()
        log_activity(f"Added location {new_location.name}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(
                {
                    "success": True,
                    "action": "create",
                    "location": {
                        "id": new_location.id,
                        "name": new_location.name,
                        "menu_name": new_location.current_menu.name
                        if new_location.current_menu
                        else None,
                    },
                }
            )
        flash("Location added successfully!")
        return redirect(url_for("locations.view_locations"))
    if request.method == "GET" and form.menu_id.data is None:
        form.menu_id.data = 0
    return render_template("locations/add_location.html", form=form)


@location.route("/locations/edit/<int:location_id>", methods=["GET", "POST"])
@login_required
def edit_location(location_id):
    """Edit an existing location."""
    location = db.session.get(Location, location_id)
    if location is None:
        abort(404)
    form = LocationForm(obj=location)
    if request.method == "GET":
        form.menu_id.data = location.current_menu_id or 0

    safe_next = _safe_next_url(request.args.get("next"))
    cancel_url = safe_next or url_for("locations.view_locations")
    action_kwargs = {"location_id": location.id}
    if safe_next:
        action_kwargs["next"] = safe_next
    form_action = url_for("locations.edit_location", **action_kwargs)

    def render_form():
        template = (
            "locations/edit_location_modal.html"
            if request.headers.get("X-Requested-With") == "XMLHttpRequest"
            else "locations/edit_location.html"
        )
        return render_template(
            template,
            form=form,
            location=location,
            form_action=form_action,
            cancel_url=cancel_url,
        )

    if form.validate_on_submit():
        menu_obj = None
        menu_id = form.menu_id.data or 0
        if menu_id:
            menu_obj = db.session.get(Menu, menu_id)
            if menu_obj is None:
                form.menu_id.errors.append("Selected menu is no longer available.")
                return render_form()
        location.name = form.name.data
        location.is_spoilage = form.is_spoilage.data
        if menu_obj is not None:
            set_location_menu(location, menu_obj)
        elif location.current_menu is not None:
            set_location_menu(location, None)
        db.session.commit()
        log_activity(f"Edited location {location.id}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify(
                {
                    "success": True,
                    "action": "update",
                    "location": {"id": location.id, "name": location.name, "menu_name": location.current_menu.name if location.current_menu else None},
                }
            )
        flash("Location updated successfully.", "success")
        redirect_kwargs = {"location_id": location.id}
        if safe_next:
            redirect_kwargs["next"] = safe_next
        return redirect(url_for("locations.edit_location", **redirect_kwargs))

    if form.menu_id.data is None:
        form.menu_id.data = location.current_menu_id or 0
    return render_form()


@location.route("/locations/<int:source_id>/copy_items", methods=["POST"])
@login_required
def copy_location_items(source_id: int):
    """Copy products and stand sheet items from one location to others.

    The target location ids can be supplied either as form data or JSON via the
    ``target_ids`` key (list) or a single ``target_id``. Any existing products
    and stand sheet items on the target locations are overwritten to match the
    source exactly.
    """
    source = db.session.get(Location, source_id)
    if source is None:
        abort(404)

    # Gather target ids from either JSON payload or form data
    if request.is_json:
        data = request.get_json(silent=True) or {}
        ids = data.get("target_ids") or (
            [data.get("target_id")] if data.get("target_id") is not None else []
        )
    else:
        ids_str = request.form.get("target_ids") or request.form.get("target_id")
        ids = [s.strip() for s in ids_str.split(",") if s.strip()] if ids_str else []

    if not ids:
        abort(400)

    target_ids = [int(tid) for tid in ids]

    # Cache source products and stand items for reuse
    source_products = list(source.products)
    source_stand_items = {
        record.item_id: record
        for record in LocationStandItem.query.filter_by(location_id=source.id).all()
    }

    processed_targets = []
    for tid in target_ids:
        target = db.session.get(Location, tid)
        if target is None:
            abort(404)

        if source.current_menu is not None:
            set_location_menu(target, source.current_menu)
            db.session.flush()
            for record in list(target.stand_items):
                source_record = source_stand_items.get(record.item_id)
                if source_record is not None:
                    record.expected_count = source_record.expected_count
                    record.purchase_gl_code_id = source_record.purchase_gl_code_id
        else:
            set_location_menu(target, None)
            db.session.flush()
            target.products = list(source_products)
            existing_items: set[int] = set()
            for product in source_products:
                for recipe_item in product.recipe_items:
                    if not recipe_item.countable:
                        continue
                    item_id = recipe_item.item_id
                    if item_id in existing_items:
                        continue
                    source_record = source_stand_items.get(item_id)
                    expected = (
                        source_record.expected_count
                        if source_record is not None
                        else 0
                    )
                    purchase_gl_code_id = (
                        source_record.purchase_gl_code_id
                        if source_record is not None
                        else recipe_item.item.purchase_gl_code_id
                    )
                    db.session.add(
                        LocationStandItem(
                            location=target,
                            item_id=item_id,
                            expected_count=expected,
                            purchase_gl_code_id=purchase_gl_code_id,
                        )
                    )
                    existing_items.add(item_id)

        processed_targets.append(str(tid))

    db.session.commit()
    log_activity(
        f"Copied location items from {source.id} to {', '.join(processed_targets)}"
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"success": True})

    flash("Items copied successfully.", "success")
    return redirect(
        url_for("locations.edit_location", location_id=target_ids[0])
    )


@location.route("/locations/<int:location_id>/stand_sheet")
@login_required
def view_stand_sheet(location_id):
    """Display the expected item counts for a location."""
    location = db.session.get(Location, location_id)
    if location is None:
        abort(404)
    stand_items = _build_location_stand_sheet_items(location)

    return render_template(
        "locations/stand_sheet.html",
        location=location,
        stand_items=stand_items,
    )


@location.route("/locations/stand_sheets/email", methods=["POST"])
@location.route(
    "/locations/<int:location_id>/stand_sheet/email",
    methods=["POST"],
)
@login_required
def email_stand_sheets(location_id: int | None = None):
    def _respond_error(message: str):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": message}), 400
        flash(message, "danger")
        redirect_target = (
            url_for("locations.view_stand_sheet", location_id=location_id)
            if location_id
            else url_for("locations.view_locations")
        )
        return redirect(redirect_target)

    email_address = (request.form.get("email") or "").strip()
    if not email_address:
        return _respond_error("Please provide an email address.")

    location_ids_raw = list(request.form.getlist("location_ids"))
    if location_id is not None:
        location_ids_raw.append(str(location_id))

    location_ids = []
    for raw in location_ids_raw:
        if not raw:
            continue
        for part in raw.split(","):
            try:
                location_ids.append(int(part.strip()))
            except ValueError:
                continue

    # Preserve the order provided by the client
    seen_ids = set()
    ordered_ids: list[int] = []
    for loc_id in location_ids:
        if loc_id not in seen_ids:
            seen_ids.add(loc_id)
            ordered_ids.append(loc_id)

    if not ordered_ids:
        return _respond_error("Please select at least one location.")

    locations = (
        Location.query.options(selectinload(Location.current_menu))
        .filter(Location.id.in_(ordered_ids))
        .all()
    )
    location_map = {loc.id: loc for loc in locations}
    ordered_locations: list[Location] = []
    for loc_id in ordered_ids:
        location_obj = location_map.get(loc_id)
        if not location_obj:
            abort(404)
        ordered_locations.append(location_obj)

    try:
        pdf_bytes = render_stand_sheet_pdf(
            [
                (
                    "locations/stand_sheet_pdf.html",
                    {
                        "location": loc,
                        "stand_items": _build_location_stand_sheet_items(loc),
                        "pdf_export": True,
                    },
                )
                for loc in ordered_locations
            ],
            base_url=request.url_root,
        )
    except Exception:
        current_app.logger.exception(
            "Failed to render stand sheet PDF for locations %s",
            ", ".join(map(str, ordered_ids)),
        )
        return _respond_error("Unable to generate the stand sheet PDF.")

    is_multiple = len(ordered_locations) > 1
    filename = (
        "stand-sheets.pdf"
        if is_multiple
        else f"location-{ordered_locations[0].id}-stand-sheet.pdf"
    )
    subject = (
        "Stand sheets"
        if is_multiple
        else f"{ordered_locations[0].name} stand sheet"
    )
    body = (
        "Attached are the stand sheets for the selected locations."
        if is_multiple
        else "Attached is the stand sheet for the requested location."
    )

    try:
        send_email(
            to_address=email_address,
            subject=subject,
            body=body,
            attachments=[(
                filename,
                pdf_bytes,
                "application/pdf",
            )],
        )
    except SMTPConfigurationError as exc:
        current_app.logger.warning(
            "SMTP configuration missing for stand sheet email: %s", exc
        )
        return _respond_error(
            "Email settings are not configured. Please update SMTP settings before sending emails."
        )
    except Exception:
        current_app.logger.exception(
            "Failed to send stand sheet email for locations %s",
            ", ".join(map(str, ordered_ids)),
        )
        return _respond_error("Unable to send the stand sheet email.")

    log_activity(
        "Emailed stand sheet(s) for locations %s to %s"
        % (", ".join(map(str, ordered_ids)), email_address)
    )
    message = (
        f"Stand sheets sent to {email_address}."
        if is_multiple
        else f"Stand sheet sent to {email_address}."
    )

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"success": True, "sent": True, "message": message})

    flash(message, "success")
    redirect_target = (
        url_for("locations.view_stand_sheet", location_id=ordered_locations[0].id)
        if not is_multiple and ordered_locations
        else url_for("locations.view_locations")
    )
    return redirect(redirect_target)


@location.route("/locations/<int:location_id>/items", methods=["GET", "POST"])
@login_required
def location_items(location_id):
    """Manage stand sheet items and GL overrides for a location."""
    location_obj = (
        Location.query.options(
            selectinload(Location.stand_items)
            .selectinload(LocationStandItem.item),
            selectinload(Location.stand_items)
            .selectinload(LocationStandItem.purchase_gl_code),
        )
        .filter_by(id=location_id)
        .first()
    )
    if location_obj is None:
        abort(404)

    # Ensure that every countable item from assigned products has a corresponding
    # ``LocationStandItem`` record so it can be displayed and managed on this
    # page. Older data may predate the automatic creation that now happens when
    # editing locations, which meant the management view could appear empty even
    # though the stand sheets contained items. Matching the stand sheet behavior
    # keeps the two views consistent.
    existing_items = {
        record.item_id: record for record in location_obj.stand_items
    }
    created = False
    for product_obj in location_obj.products:
        for recipe_item in product_obj.recipe_items:
            if not recipe_item.countable:
                continue
            if recipe_item.item_id in existing_items:
                continue
            new_record = LocationStandItem(
                location_id=location_id,
                item_id=recipe_item.item_id,
                expected_count=0,
                purchase_gl_code_id=recipe_item.item.purchase_gl_code_id,
            )
            db.session.add(new_record)
            existing_items[recipe_item.item_id] = new_record
            created = True
    if created:
        db.session.commit()

    protected_item_ids = _protected_location_item_ids(location_obj)
    form = CSRFOnlyForm()
    add_form = LocationItemAddForm()
    delete_form = DeleteForm()
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()

    available_choices = [
        (item.id, item.name)
        for item in Item.query.filter_by(archived=False)
        .order_by(Item.name)
        .all()
        if item.id not in existing_items
    ]
    add_form.item_id.choices = available_choices

    query = (
        LocationStandItem.query.join(Item)
        .outerjoin(GLCode, LocationStandItem.purchase_gl_code_id == GLCode.id)
        .options(
            selectinload(LocationStandItem.item),
            selectinload(LocationStandItem.purchase_gl_code),
        )
        .filter(LocationStandItem.location_id == location_id)
        .order_by(Item.name)
    )

    if form.validate_on_submit():
        updated = 0
        for record in query.paginate(page=page, per_page=per_page).items:
            field_name = f"location_gl_code_{record.item_id}"
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
            flash("Item GL codes updated successfully.", "success")
        else:
            flash("No changes were made to item GL codes.", "info")
        return redirect(
            url_for(
                "locations.location_items",
                location_id=location_id,
                page=page,
                per_page=per_page,
            )
        )

    entries = query.paginate(page=page, per_page=per_page)
    for record in entries.items:
        record.is_protected = record.item_id in protected_item_ids
    total_expected = (
        db.session.query(db.func.sum(LocationStandItem.expected_count))
        .filter_by(location_id=location_id)
        .scalar()
        or 0
    )
    return render_template(
        "locations/location_items.html",
        location=location_obj,
        entries=entries,
        total=total_expected,
        per_page=per_page,
        form=form,
        add_form=add_form,
        delete_form=delete_form,
        can_add_items=bool(available_choices),
        purchase_gl_codes=ItemForm._fetch_purchase_gl_codes(),
        pagination_args=build_pagination_args(per_page),
    )


@location.route("/locations/<int:location_id>/items/add", methods=["POST"])
@login_required
def add_location_item(location_id: int):
    """Add a standalone item to a location's stand sheet."""

    location_obj = (
        Location.query.options(selectinload(Location.stand_items))
        .filter_by(id=location_id)
        .first()
    )
    if location_obj is None:
        abort(404)

    add_form = LocationItemAddForm()
    page = request.form.get("page")
    per_page = request.form.get("per_page")

    existing_item_ids = {
        record.item_id for record in location_obj.stand_items
    }
    available_choices = [
        (item.id, item.name)
        for item in Item.query.filter_by(archived=False)
        .order_by(Item.name)
        .all()
        if item.id not in existing_item_ids
    ]
    add_form.item_id.choices = available_choices

    if not available_choices:
        flash("There are no additional items available to add.", "info")
        return _location_items_redirect(location_id, page, per_page)

    if not add_form.validate_on_submit():
        flash("Unable to add item to the location.", "error")
        return _location_items_redirect(location_id, page, per_page)

    item_id = add_form.item_id.data
    if item_id in existing_item_ids:
        flash("This item is already tracked at the location.", "info")
        return _location_items_redirect(location_id, page, per_page)

    item = db.session.get(Item, item_id)
    if item is None or item.archived:
        flash("Selected item is no longer available.", "error")
        return _location_items_redirect(location_id, page, per_page)

    expected = add_form.expected_count.data or 0
    item_name = item.name
    new_record = LocationStandItem(
        location_id=location_id,
        item_id=item_id,
        expected_count=float(expected),
        purchase_gl_code_id=item.purchase_gl_code_id,
    )
    db.session.add(new_record)
    db.session.commit()
    log_activity(
        f"Added item {item_name} to location {location_obj.name}"
    )
    flash("Item added to location.", "success")
    return _location_items_redirect(location_id, page, per_page)


@location.route(
    "/locations/<int:location_id>/items/<int:item_id>/delete",
    methods=["POST"],
)
@login_required
def delete_location_item(location_id: int, item_id: int):
    """Remove a removable item from a location's stand sheet."""

    location_obj = (
        Location.query.options(selectinload(Location.products))
        .filter_by(id=location_id)
        .first()
    )
    if location_obj is None:
        abort(404)

    form = DeleteForm()
    page = request.form.get("page")
    per_page = request.form.get("per_page")
    if not form.validate_on_submit():
        flash("Unable to remove the item from the location.", "error")
        return _location_items_redirect(location_id, page, per_page)

    record = LocationStandItem.query.filter_by(
        location_id=location_id, item_id=item_id
    ).first()
    if record is None:
        flash("Item not found on location.", "error")
        return _location_items_redirect(location_id, page, per_page)

    protected_item_ids = _protected_location_item_ids(location_obj)
    if item_id in protected_item_ids:
        flash(
            "This item is required by a product recipe and cannot be removed.",
            "error",
        )
        return _location_items_redirect(location_id, page, per_page)

    item_name = record.item.name
    db.session.delete(record)
    db.session.commit()
    log_activity(
        f"Removed item {item_name} from location {location_obj.name}"
    )
    flash("Item removed from location.", "success")
    return _location_items_redirect(location_id, page, per_page)


@location.route("/locations")
@login_required
def view_locations():
    """List all locations."""
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    name_query = request.args.get("name_query", "")
    match_mode = request.args.get("match_mode", "contains")
    archived = request.args.get("archived", "active")
    raw_menu_ids = request.args.getlist("menu_ids")
    spoilage_filter = request.args.get("spoilage", "all")

    include_no_menu = False
    menu_ids: set[int] = set()
    for raw_value in raw_menu_ids:
        try:
            menu_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if menu_id == 0:
            include_no_menu = True
        else:
            menu_ids.add(menu_id)

    valid_spoilage_filters = {"all", "spoilage", "non_spoilage"}
    if spoilage_filter not in valid_spoilage_filters:
        spoilage_filter = "all"

    query = Location.query.options(selectinload(Location.current_menu))
    if archived == "active":
        query = query.filter(Location.archived.is_(False))
    elif archived == "archived":
        query = query.filter(Location.archived.is_(True))

    if name_query:
        if match_mode == "exact":
            query = query.filter(Location.name == name_query)
        elif match_mode == "startswith":
            query = query.filter(Location.name.like(f"{name_query}%"))
        elif match_mode == "not_contains":
            query = query.filter(Location.name.notlike(f"%{name_query}%"))
        else:
            query = query.filter(Location.name.like(f"%{name_query}%"))

    if include_no_menu and menu_ids:
        query = query.filter(
            or_(
                Location.current_menu_id.in_(menu_ids),
                Location.current_menu_id.is_(None),
            )
        )
    elif include_no_menu:
        query = query.filter(Location.current_menu_id.is_(None))
    elif menu_ids:
        query = query.filter(Location.current_menu_id.in_(menu_ids))

    if spoilage_filter == "spoilage":
        query = query.filter(Location.is_spoilage.is_(True))
    elif spoilage_filter == "non_spoilage":
        query = query.filter(Location.is_spoilage.is_(False))

    locations = query.order_by(Location.name).paginate(
        page=page, per_page=per_page
    )
    menus = Menu.query.order_by(Menu.name).all()
    delete_form = DeleteForm()
    return render_template(
        "locations/view_locations.html",
        locations=locations,
        delete_form=delete_form,
        name_query=name_query,
        match_mode=match_mode,
        archived=archived,
        menus=menus,
        selected_menu_ids=menu_ids,
        include_no_menu=include_no_menu,
        spoilage_filter=spoilage_filter,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


def _parse_location_ids(raw_value: str) -> list[int]:
    ids: list[int] = []
    for chunk in raw_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            ids.append(int(chunk))
        except ValueError:
            raise ValueError("Invalid location identifier.") from None
    return ids


def _render_location_bulk_form(form: BulkLocationUpdateForm):
    return render_template("locations/bulk_update_form.html", form=form)


@location.route("/locations/bulk-update", methods=["GET", "POST"])
@login_required
def bulk_update_locations():
    """Apply updates to multiple locations."""

    form = BulkLocationUpdateForm()
    if request.method == "GET":
        raw_ids = request.args.getlist("ids") or request.args.getlist("id")
        try:
            selected_ids = [int(value) for value in raw_ids if int(value)]
        except ValueError:
            abort(400)
        if not selected_ids:
            abort(400)
        form.selected_ids.data = ",".join(str(value) for value in selected_ids)
        return _render_location_bulk_form(form)

    if form.validate_on_submit():
        try:
            selected_ids = _parse_location_ids(form.selected_ids.data or "")
        except ValueError:
            form.selected_ids.errors.append("Unable to determine selected locations.")
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {
                        "success": False,
                        "form_html": _render_location_bulk_form(form),
                    }
                )
            flash("Unable to determine selected locations for update.", "error")
            return redirect(url_for("locations.view_locations"))

        if not selected_ids:
            form.selected_ids.errors.append(
                "Select at least one location to update."
            )
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {
                        "success": False,
                        "form_html": _render_location_bulk_form(form),
                    }
                )
            flash("Select at least one location to update.", "error")
            return redirect(url_for("locations.view_locations"))

        locations = (
            Location.query.options(selectinload(Location.current_menu))
            .filter(Location.id.in_(selected_ids))
            .order_by(Location.id)
            .all()
        )
        if len(locations) != len(set(selected_ids)):
            form.selected_ids.errors.append(
                "Some selected locations are no longer available."
            )
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify(
                    {
                        "success": False,
                        "form_html": _render_location_bulk_form(form),
                    }
                )
            flash("Some selected locations are no longer available.", "error")
            return redirect(url_for("locations.view_locations"))

        apply_name = form.apply_name.data
        apply_menu = form.apply_menu_id.data
        apply_spoilage = form.apply_is_spoilage.data
        apply_archived = form.apply_archived.data

        new_name = form.name.data if apply_name else None
        new_menu_id = form.menu_id.data if apply_menu else None
        new_is_spoilage = form.is_spoilage.data if apply_spoilage else None
        new_archived = form.archived.data if apply_archived else None

        if apply_name:
            if len(selected_ids) > 1:
                form.name.errors.append(
                    "Cannot assign the same name to multiple locations."
                )
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify(
                        {
                            "success": False,
                            "form_html": _render_location_bulk_form(form),
                        }
                    )
                flash(
                    "Cannot assign the same name to multiple locations.",
                    "error",
                )
                return redirect(url_for("locations.view_locations"))
            conflict = (
                Location.query.filter(Location.name == new_name)
                .filter(~Location.id.in_(selected_ids))
                .first()
            )
            if conflict:
                form.name.errors.append("A location with that name already exists.")
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify(
                        {
                            "success": False,
                            "form_html": _render_location_bulk_form(form),
                        }
                    )
                flash("A location with that name already exists.", "error")
                return redirect(url_for("locations.view_locations"))

        menu_obj = None
        if apply_menu and new_menu_id:
            menu_obj = db.session.get(Menu, new_menu_id)
            if menu_obj is None:
                form.menu_id.errors.append("Selected menu is no longer available.")
                if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                    return jsonify(
                        {
                            "success": False,
                            "form_html": _render_location_bulk_form(form),
                        }
                    )
                flash("Selected menu is no longer available.", "error")
                return redirect(url_for("locations.view_locations"))

        with db.session.begin_nested():
            for location_obj in locations:
                if apply_name:
                    location_obj.name = new_name
                if apply_spoilage:
                    location_obj.is_spoilage = new_is_spoilage
                if apply_archived:
                    location_obj.archived = new_archived
                if apply_menu:
                    if new_menu_id and menu_obj is not None:
                        set_location_menu(location_obj, menu_obj)
                    else:
                        set_location_menu(location_obj, None)
        db.session.commit()

        refreshed_locations = (
            Location.query.options(selectinload(Location.current_menu))
            .filter(Location.id.in_(selected_ids))
            .order_by(Location.id)
            .all()
        )

        log_activity(
            "Bulk updated locations: "
            + ", ".join(str(loc.id) for loc in refreshed_locations)
        )

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            delete_form = DeleteForm()
            rows = [
                {
                    "id": location_obj.id,
                    "html": render_template(
                        "locations/_location_row.html",
                        location=location_obj,
                        delete_form=delete_form,
                    ),
                }
                for location_obj in refreshed_locations
            ]
            return jsonify({"success": True, "rows": rows})

        flash("Locations updated successfully.", "success")
        return redirect(url_for("locations.view_locations"))

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(
            {"success": False, "form_html": _render_location_bulk_form(form)}
        )

    return _render_location_bulk_form(form)


@location.route("/locations/delete/<int:location_id>", methods=["POST"])
@login_required
def delete_location(location_id):
    """Remove a location from the database."""
    location = db.session.get(Location, location_id)
    if location is None:
        abort(404)
    location.archived = True
    db.session.commit()
    log_activity(f"Archived location {location.id}")
    flash("Location archived successfully!")
    return redirect(url_for("locations.view_locations"))
