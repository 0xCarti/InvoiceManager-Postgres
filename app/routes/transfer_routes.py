"""Routes for handling transfer functionality."""

# flake8: noqa


import re
from datetime import datetime

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
from sqlalchemy import func, tuple_

from app import db, socketio
from app.forms import ConfirmForm, DateRangeForm, TransferForm
from app.models import (
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Transfer,
    TransferItem,
    User,
)
from app.utils.activity import log_activity
from app.utils.numeric import coerce_float
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.sms import send_sms
from app.utils.text import build_text_match_predicate, normalize_request_text_filter
from app.utils.text import normalize_request_text_filter

transfer = Blueprint("transfer", __name__)


def _extract_transfer_items(prefix: str):
    """Parse dynamic transfer item inputs from ``request.form``.

    Parameters
    ----------
    prefix:
        The common prefix used for the transfer item inputs (e.g. ``"items"``
        or ``"add-items"``).

    Returns
    -------
    list[dict[str, object]]
        A list of dictionaries describing each valid row. Each dictionary
        contains the ``Item`` instance, the combined ``total_quantity``, and
        the captured unit selection/quantity details.
    """

    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)-item$")
    results = []

    for key in request.form.keys():
        match = pattern.match(key)
        if not match:
            continue
        index = match.group(1)
        item_id = request.form.get(f"{prefix}-{index}-item", type=int)
        if not item_id:
            continue
        item = db.session.get(Item, item_id)
        if item is None:
            continue

        unit_quantity = coerce_float(
            request.form.get(f"{prefix}-{index}-quantity")
        )
        base_quantity = coerce_float(
            request.form.get(f"{prefix}-{index}-base_quantity")
        )
        unit_id = request.form.get(f"{prefix}-{index}-unit", type=int)

        factor = 1.0
        if unit_id:
            unit = db.session.get(ItemUnit, unit_id)
            if unit:
                factor = unit.factor

        total_quantity = 0.0
        unit_base_quantity = None
        if unit_quantity is not None:
            unit_base_quantity = unit_quantity * factor
            total_quantity += unit_base_quantity
        if base_quantity is not None:
            total_quantity += base_quantity

        if total_quantity != 0:
            results.append(
                {
                    "item": item,
                    "total_quantity": total_quantity,
                    "unit_id": unit_id or None,
                    "unit_quantity": unit_quantity,
                    "base_quantity": base_quantity,
                    "unit_base_quantity": unit_base_quantity,
                }
            )

    return results


def _build_transfer_item_quantities(transfer_items, multiplier):
    quantities = {}
    for transfer_item in transfer_items:
        if multiplier >= 0:
            quantity = (
                transfer_item.quantity - transfer_item.completed_quantity
            )
        else:
            quantity = transfer_item.completed_quantity
        if quantity:
            quantities[transfer_item.id] = quantity
    return quantities


def _sync_transfer_completed(transfer_obj):
    transfer_obj.completed = all(
        transfer_item.completed_quantity >= transfer_item.quantity
        for transfer_item in transfer_obj.transfer_items
    )


def check_negative_transfer(
    transfer_obj, multiplier=1, transfer_items=None, quantities=None
):
    """Return warnings if a transfer would cause negative inventory."""
    warnings = []
    transfer_items = transfer_items or transfer_obj.transfer_items
    quantities = quantities or {}
    pairs = {
        (transfer_obj.from_location_id, ti.item_id)
        for ti in transfer_items
    } | {
        (transfer_obj.to_location_id, ti.item_id)
        for ti in transfer_items
    }
    if not pairs:
        return warnings
    records = LocationStandItem.query.filter(
        tuple_(LocationStandItem.location_id, LocationStandItem.item_id).in_(
            pairs
        )
    ).all()
    indexed = {(r.location_id, r.item_id): r for r in records}

    for ti in transfer_items:
        quantity = quantities.get(ti.id, ti.quantity)
        if not quantity:
            continue
        from_record = indexed.get((transfer_obj.from_location_id, ti.item_id))
        current_from = from_record.expected_count if from_record else 0
        new_from = current_from - multiplier * quantity
        if new_from < 0:
            item = db.session.get(Item, ti.item_id)
            item_name = item.name if item else ti.item_name
            from_name = (
                transfer_obj.from_location.name
                if transfer_obj.from_location
                else transfer_obj.from_location_name
            )
            warnings.append(
                f"Transfer will result in negative inventory for {item_name} at {from_name}"
            )

        to_record = indexed.get((transfer_obj.to_location_id, ti.item_id))
        current_to = to_record.expected_count if to_record else 0
        new_to = current_to + multiplier * quantity
        if new_to < 0:
            item = db.session.get(Item, ti.item_id)
            item_name = item.name if item else ti.item_name
            to_name = (
                transfer_obj.to_location.name
                if transfer_obj.to_location
                else transfer_obj.to_location_name
            )
            warnings.append(
                f"Transfer will result in negative inventory for {item_name} at {to_name}"
            )
    return warnings


def update_expected_counts(
    transfer_obj, multiplier=1, transfer_items=None, quantities=None
):
    """Update expected counts for locations involved in a transfer."""
    transfer_items = transfer_items or transfer_obj.transfer_items
    quantities = quantities or {}
    pairs = {
        (transfer_obj.from_location_id, ti.item_id)
        for ti in transfer_items
    } | {
        (transfer_obj.to_location_id, ti.item_id)
        for ti in transfer_items
    }
    if not pairs:
        return
    records = LocationStandItem.query.filter(
        tuple_(LocationStandItem.location_id, LocationStandItem.item_id).in_(
            pairs
        )
    ).all()
    indexed = {(r.location_id, r.item_id): r for r in records}

    for ti in transfer_items:
        quantity = quantities.get(ti.id, ti.quantity)
        if not quantity:
            continue
        item_obj = db.session.get(Item, ti.item_id)

        from_key = (transfer_obj.from_location_id, ti.item_id)
        from_record = indexed.get(from_key)
        if not from_record:
            from_record = LocationStandItem(
                location_id=transfer_obj.from_location_id,
                item_id=ti.item_id,
                expected_count=0,
                purchase_gl_code_id=(
                    item_obj.purchase_gl_code_id if item_obj else None
                ),
            )
            db.session.add(from_record)
            indexed[from_key] = from_record
        from_record.expected_count = (
            from_record.expected_count - multiplier * quantity
        )

        to_key = (transfer_obj.to_location_id, ti.item_id)
        to_record = indexed.get(to_key)
        if not to_record:
            to_record = LocationStandItem(
                location_id=transfer_obj.to_location_id,
                item_id=ti.item_id,
                expected_count=0,
                purchase_gl_code_id=(
                    item_obj.purchase_gl_code_id if item_obj else None
                ),
            )
            db.session.add(to_record)
            indexed[to_key] = to_record
        to_record.expected_count = (
            to_record.expected_count + multiplier * quantity
        )


@transfer.route("/transfers", methods=["GET"])
@login_required
def view_transfers():
    """Show transfers with optional filtering."""
    filter_option = request.args.get("filter", "not_completed")
    user_id = request.args.get("user_id", type=int)
    transfer_id = request.args.get(
        "transfer_id", "", type=int
    )  # Optional: Search by Transfer ID
    from_location_name = normalize_request_text_filter(
        request.args.get("from_location")
    )  # Optional: Search by From Location
    to_location_name = normalize_request_text_filter(
        request.args.get("to_location")
    )  # Optional: Search by To Location
    page = request.args.get("page", 1, type=int)

    query = Transfer.query
    if user_id:
        query = query.filter(Transfer.user_id == user_id)
    if transfer_id != "":
        query = query.filter(Transfer.id == transfer_id)

    if from_location_name:
        query = query.join(
            Location, Transfer.from_location_id == Location.id
        ).filter(
            build_text_match_predicate(
                Location.name, from_location_name, "contains"
            )
        )

    if to_location_name:
        query = query.join(
            Location, Transfer.to_location_id == Location.id
        ).filter(
            build_text_match_predicate(Location.name, to_location_name, "contains")
        )

    if filter_option == "completed":
        query = query.filter(Transfer.completed)
    elif filter_option == "not_completed":
        query = query.filter(~Transfer.completed)

    per_page = get_per_page()
    transfers = query.paginate(page=page, per_page=per_page)

    form = TransferForm()
    add_form = TransferForm(prefix="add")
    edit_form = TransferForm(prefix="edit")
    return render_template(
        "transfers/view_transfers.html",
        transfers=transfers,
        form=form,
        add_form=add_form,
        edit_form=edit_form,
        per_page=per_page,
        pagination_args=build_pagination_args(
            per_page,
            extra_params={"user_id": user_id},
        ),
    )


@transfer.route("/transfers/add", methods=["GET", "POST"])
@login_required
def add_transfer():
    """Create a transfer between locations."""
    form = TransferForm()
    if form.validate_on_submit():
        item_entries = _extract_transfer_items("items")
        if not item_entries:
            form.errors.setdefault("items", []).append(
                "Add at least one item with a quantity."
            )
            flash("Please add at least one item to the transfer.", "error")
        else:
            from_location = db.session.get(
                Location, form.from_location_id.data
            )
            to_location = db.session.get(Location, form.to_location_id.data)
            transfer = Transfer(
                from_location_id=form.from_location_id.data,
                to_location_id=form.to_location_id.data,
                user_id=current_user.id,
                from_location_name=from_location.name if from_location else "",
                to_location_name=to_location.name if to_location else "",
            )
            db.session.add(transfer)

            for entry in item_entries:
                item = entry["item"]
                transfer.transfer_items.append(
                    TransferItem(
                        transfer=transfer,
                        item_id=item.id,
                        quantity=entry["total_quantity"],
                        unit_id=entry["unit_id"],
                        unit_quantity=entry["unit_quantity"],
                        base_quantity=entry["base_quantity"],
                        item_name=item.name,
                    )
                )

            db.session.commit()
            log_activity(f"Added transfer {transfer.id}")

            socketio.emit("new_transfer", {"message": "New transfer added"})

            try:
                notify_users = User.query.filter_by(notify_transfers=True).all()
                for user in notify_users:
                    if user.phone_number:
                        send_sms(
                            user.phone_number, f"Transfer {transfer.id} created"
                        )
            except Exception:
                pass

            flash("Transfer added successfully!", "success")
            return redirect(url_for("transfer.view_transfers"))
    elif form.errors:
        flash("There was an error submitting the transfer.", "error")

    return render_template("transfers/add_transfer.html", form=form)


@transfer.route("/transfers/ajax_add", methods=["POST"])
@login_required
def ajax_add_transfer():
    form = TransferForm(prefix="add")
    if form.validate_on_submit():
        item_entries = _extract_transfer_items("add-items")
        if not item_entries:
            form.errors.setdefault("items", []).append(
                "Add at least one item with a quantity."
            )
        else:
            from_location = db.session.get(
                Location, form.from_location_id.data
            )
            to_location = db.session.get(Location, form.to_location_id.data)
            transfer = Transfer(
                from_location_id=form.from_location_id.data,
                to_location_id=form.to_location_id.data,
                user_id=current_user.id,
                from_location_name=from_location.name if from_location else "",
                to_location_name=to_location.name if to_location else "",
            )
            db.session.add(transfer)

            for entry in item_entries:
                item = entry["item"]
                transfer.transfer_items.append(
                    TransferItem(
                        transfer=transfer,
                        item_id=item.id,
                        quantity=entry["total_quantity"],
                        unit_id=entry["unit_id"],
                        unit_quantity=entry["unit_quantity"],
                        base_quantity=entry["base_quantity"],
                        item_name=item.name,
                    )
                )

            db.session.commit()
            log_activity(f"Added transfer {transfer.id}")
            socketio.emit("new_transfer", {"message": "New transfer added"})
            try:
                notify_users = User.query.filter_by(notify_transfers=True).all()
                for user in notify_users:
                    if user.phone_number:
                        send_sms(
                            user.phone_number,
                            f"Transfer {transfer.id} created",
                        )
            except Exception:
                pass
            row_html = render_template(
                "transfers/_transfer_row.html",
                transfer=transfer,
                form=TransferForm(),
            )
            return jsonify(success=True, html=row_html)
    return jsonify(success=False, errors=form.errors), 400


@transfer.route("/transfers/edit/<int:transfer_id>", methods=["GET", "POST"])
@login_required
def edit_transfer(transfer_id):
    """Update an existing transfer."""
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None:
        abort(404)
    form = TransferForm(obj=transfer)

    if form.validate_on_submit():
        item_entries = _extract_transfer_items("items")
        if not item_entries:
            form.errors.setdefault("items", []).append(
                "Add at least one item with a quantity."
            )
            flash("Please add at least one item to the transfer.", "error")
        else:
            from_location = db.session.get(
                Location, form.from_location_id.data
            )
            to_location = db.session.get(Location, form.to_location_id.data)
            transfer.from_location_id = form.from_location_id.data
            transfer.to_location_id = form.to_location_id.data
            transfer.from_location_name = (
                from_location.name if from_location else ""
            )
            transfer.to_location_name = to_location.name if to_location else ""

            TransferItem.query.filter_by(transfer_id=transfer.id).delete()

            for entry in item_entries:
                item = entry["item"]
                transfer.transfer_items.append(
                    TransferItem(
                        transfer=transfer,
                        item_id=item.id,
                        quantity=entry["total_quantity"],
                        unit_id=entry["unit_id"],
                        unit_quantity=entry["unit_quantity"],
                        base_quantity=entry["base_quantity"],
                        item_name=item.name,
                    )
                )

            db.session.commit()
            log_activity(f"Edited transfer {transfer.id}")
            flash("Transfer updated successfully!", "success")
            return redirect(url_for("transfer.view_transfers"))
    elif form.errors:
        flash("There was an error submitting the transfer.", "error")

    # For GET requests or if the form doesn't validate, pass existing items to the template
    items = []
    for transfer_item in transfer.transfer_items:
        item_obj = transfer_item.item
        items.append(
            {
                "id": transfer_item.item_id,
                "name": item_obj.name if item_obj else transfer_item.item_name,
                "quantity": transfer_item.quantity,
                "unit_id": transfer_item.unit_id,
                "unit_quantity": transfer_item.unit_quantity,
                "base_quantity": transfer_item.base_quantity,
            }
        )
    return render_template(
        "transfers/edit_transfer.html",
        form=form,
        transfer=transfer,
        items=items,
    )


@transfer.route("/transfers/<int:transfer_id>/json")
@login_required
def transfer_json(transfer_id):
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None:
        abort(404)
    items = []
    for ti in transfer.transfer_items:
        item_obj = ti.item
        items.append(
            {
                "id": ti.item_id,
                "name": item_obj.name if item_obj else ti.item_name,
                "quantity": ti.quantity,
                "completed_quantity": ti.completed_quantity,
                "is_completed": ti.completed_quantity >= ti.quantity,
                "unit_id": ti.unit_id,
                "unit_quantity": ti.unit_quantity,
                "base_quantity": ti.base_quantity,
            }
        )
    return jsonify(
        {
            "id": transfer.id,
            "from_location_id": transfer.from_location_id,
            "to_location_id": transfer.to_location_id,
            "items": items,
        }
    )


@transfer.route("/transfers/ajax_edit/<int:transfer_id>", methods=["POST"])
@login_required
def ajax_edit_transfer(transfer_id):
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None:
        abort(404)
    form = TransferForm(prefix="edit")
    if form.validate_on_submit():
        item_entries = _extract_transfer_items("edit-items")
        if not item_entries:
            item_entries = _extract_transfer_items("items")
        if not item_entries:
            form.errors.setdefault("items", []).append(
                "Add at least one item with a quantity."
            )
        else:
            from_location = db.session.get(
                Location, form.from_location_id.data
            )
            to_location = db.session.get(Location, form.to_location_id.data)
            transfer.from_location_id = form.from_location_id.data
            transfer.to_location_id = form.to_location_id.data
            transfer.from_location_name = (
                from_location.name if from_location else ""
            )
            transfer.to_location_name = to_location.name if to_location else ""
            TransferItem.query.filter_by(transfer_id=transfer.id).delete()

            for entry in item_entries:
                item = entry["item"]
                transfer.transfer_items.append(
                    TransferItem(
                        transfer=transfer,
                        item_id=item.id,
                        quantity=entry["total_quantity"],
                        unit_id=entry["unit_id"],
                        unit_quantity=entry["unit_quantity"],
                        base_quantity=entry["base_quantity"],
                        item_name=item.name,
                    )
                )
            db.session.commit()
            log_activity(f"Edited transfer {transfer.id}")
            row_html = render_template(
                "transfers/_transfer_row.html",
                transfer=transfer,
                form=TransferForm(),
            )
            return jsonify(success=True, html=row_html, id=transfer.id)
    return jsonify(success=False, errors=form.errors), 400


@transfer.route("/transfers/delete/<int:transfer_id>", methods=["POST"])
@login_required
def delete_transfer(transfer_id):
    """Permanently remove a transfer."""
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None:
        abort(404)
    if transfer.completed:
        update_expected_counts(transfer, multiplier=-1)
    db.session.delete(transfer)
    db.session.commit()
    log_activity(f"Deleted transfer {transfer.id}")
    flash("Transfer deleted successfully!", "success")
    return redirect(url_for("transfer.view_transfers"))


@transfer.route(
    "/transfers/complete/<int:transfer_id>", methods=["GET", "POST"]
)
@login_required
def complete_transfer(transfer_id):
    """Mark a transfer as completed."""
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None:
        abort(404)
    transfer_items = [
        transfer_item
        for transfer_item in transfer.transfer_items
        if transfer_item.quantity > transfer_item.completed_quantity
    ]
    quantities = _build_transfer_item_quantities(transfer_items, multiplier=1)
    warnings = check_negative_transfer(
        transfer,
        multiplier=1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    form = ConfirmForm()
    if warnings and request.method == "GET":
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.complete_transfer", transfer_id=transfer_id
            ),
            cancel_url=url_for("transfer.view_transfers"),
            title="Confirm Transfer Completion",
        )
    if warnings and not form.validate_on_submit():
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.complete_transfer", transfer_id=transfer_id
            ),
            cancel_url=url_for("transfer.view_transfers"),
            title="Confirm Transfer Completion",
        )
    completed_at = datetime.utcnow()
    for transfer_item in transfer_items:
        transfer_item.completed_quantity = transfer_item.quantity
        transfer_item.completed_at = completed_at
        transfer_item.completed_by_id = current_user.id
    transfer.completed = True
    update_expected_counts(
        transfer,
        multiplier=1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    db.session.commit()
    log_activity(f"Completed transfer {transfer.id}")
    flash("Transfer marked as complete!", "success")
    return redirect(url_for("transfer.view_transfers"))


@transfer.route(
    "/transfers/items/complete/<int:transfer_item_id>",
    methods=["GET", "POST"],
)
@login_required
def complete_transfer_item(transfer_item_id):
    """Mark a transfer item as completed."""
    transfer_item = db.session.get(TransferItem, transfer_item_id)
    if transfer_item is None:
        abort(404)
    transfer = transfer_item.transfer
    if transfer_item.quantity <= transfer_item.completed_quantity:
        flash("Transfer item already completed.", "info")
        return redirect(
            url_for("transfer.view_transfer", transfer_id=transfer.id)
        )
    transfer_items = [transfer_item]
    quantities = _build_transfer_item_quantities(transfer_items, multiplier=1)
    warnings = check_negative_transfer(
        transfer,
        multiplier=1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    form = ConfirmForm()
    if warnings and request.method == "GET":
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.complete_transfer_item",
                transfer_item_id=transfer_item_id,
            ),
            cancel_url=url_for(
                "transfer.view_transfer", transfer_id=transfer.id
            ),
            title="Confirm Transfer Item Completion",
        )
    if warnings and not form.validate_on_submit():
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.complete_transfer_item",
                transfer_item_id=transfer_item_id,
            ),
            cancel_url=url_for(
                "transfer.view_transfer", transfer_id=transfer.id
            ),
            title="Confirm Transfer Item Completion",
        )
    completed_at = datetime.utcnow()
    transfer_item.completed_quantity = transfer_item.quantity
    transfer_item.completed_at = completed_at
    transfer_item.completed_by_id = current_user.id
    update_expected_counts(
        transfer,
        multiplier=1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    _sync_transfer_completed(transfer)
    db.session.commit()
    log_activity(
        f"Completed transfer item {transfer_item.id} on transfer {transfer.id}"
    )
    flash("Transfer item marked as complete!", "success")
    return redirect(url_for("transfer.view_transfer", transfer_id=transfer.id))


@transfer.route(
    "/transfers/uncomplete/<int:transfer_id>", methods=["GET", "POST"]
)
@login_required
def uncomplete_transfer(transfer_id):
    """Revert a transfer to not completed."""
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None:
        abort(404)
    transfer_items = [
        transfer_item
        for transfer_item in transfer.transfer_items
        if transfer_item.completed_quantity
    ]
    quantities = _build_transfer_item_quantities(transfer_items, multiplier=-1)
    warnings = check_negative_transfer(
        transfer,
        multiplier=-1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    form = ConfirmForm()
    if warnings and request.method == "GET":
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.uncomplete_transfer", transfer_id=transfer_id
            ),
            cancel_url=url_for("transfer.view_transfers"),
            title="Confirm Transfer Incomplete",
        )
    if warnings and not form.validate_on_submit():
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.uncomplete_transfer", transfer_id=transfer_id
            ),
            cancel_url=url_for("transfer.view_transfers"),
            title="Confirm Transfer Incomplete",
        )
    transfer.completed = False
    for transfer_item in transfer_items:
        transfer_item.completed_quantity = 0.0
        transfer_item.completed_at = None
        transfer_item.completed_by_id = None
    update_expected_counts(
        transfer,
        multiplier=-1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    db.session.commit()
    log_activity(f"Uncompleted transfer {transfer.id}")
    flash("Transfer marked as not completed.", "success")
    return redirect(url_for("transfer.view_transfers"))


@transfer.route(
    "/transfers/items/uncomplete/<int:transfer_item_id>",
    methods=["GET", "POST"],
)
@login_required
def uncomplete_transfer_item(transfer_item_id):
    """Revert a transfer item to not completed."""
    transfer_item = db.session.get(TransferItem, transfer_item_id)
    if transfer_item is None:
        abort(404)
    transfer = transfer_item.transfer
    if not transfer_item.completed_quantity:
        flash("Transfer item is already not completed.", "info")
        return redirect(
            url_for("transfer.view_transfer", transfer_id=transfer.id)
        )
    transfer_items = [transfer_item]
    quantities = _build_transfer_item_quantities(transfer_items, multiplier=-1)
    warnings = check_negative_transfer(
        transfer,
        multiplier=-1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    form = ConfirmForm()
    if warnings and request.method == "GET":
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.uncomplete_transfer_item",
                transfer_item_id=transfer_item_id,
            ),
            cancel_url=url_for(
                "transfer.view_transfer", transfer_id=transfer.id
            ),
            title="Confirm Transfer Item Incomplete",
        )
    if warnings and not form.validate_on_submit():
        return render_template(
            "confirm_action.html",
            form=form,
            warnings=warnings,
            action_url=url_for(
                "transfer.uncomplete_transfer_item",
                transfer_item_id=transfer_item_id,
            ),
            cancel_url=url_for(
                "transfer.view_transfer", transfer_id=transfer.id
            ),
            title="Confirm Transfer Item Incomplete",
        )
    transfer_item.completed_quantity = 0.0
    transfer_item.completed_at = None
    transfer_item.completed_by_id = None
    update_expected_counts(
        transfer,
        multiplier=-1,
        transfer_items=transfer_items,
        quantities=quantities,
    )
    _sync_transfer_completed(transfer)
    db.session.commit()
    log_activity(
        f"Uncompleted transfer item {transfer_item.id} on transfer {transfer.id}"
    )
    flash("Transfer item marked as not completed.", "success")
    return redirect(url_for("transfer.view_transfer", transfer_id=transfer.id))


@transfer.route("/transfers/view/<int:transfer_id>", methods=["GET"])
@login_required
def view_transfer(transfer_id):
    """Show details for a single transfer."""
    transfer = db.session.get(Transfer, transfer_id)
    if transfer is None:
        abort(404)
    transfer_items = TransferItem.query.filter_by(
        transfer_id=transfer.id
    ).all()
    return render_template(
        "transfers/view_transfer.html",
        transfer=transfer,
        transfer_items=transfer_items,
    )


@transfer.route("/transfers/generate_report", methods=["GET", "POST"])
@login_required
def generate_report():
    """Generate a transfer summary over a date range."""
    form = DateRangeForm()
    if form.validate_on_submit():
        start_datetime = form.start_datetime.data
        end_datetime = form.end_datetime.data
        from_location_ids = list(form.from_location_ids.data or [])
        to_location_ids = list(form.to_location_ids.data or [])

        location_lookup = {
            choice_id: label for choice_id, label in form.from_location_ids.choices
        }

        # Alias for "from" and "to" locations
        from_location = db.aliased(Location)
        to_location = db.aliased(Location)

        aggregated_transfers = (
            db.session.query(
                from_location.name.label("from_location_name"),
                to_location.name.label("to_location_name"),
                Item.name.label("item_name"),
                func.sum(TransferItem.completed_quantity).label(
                    "total_quantity"
                ),
            )
            .select_from(Transfer)
            .join(TransferItem, Transfer.id == TransferItem.transfer_id)
            .join(Item, TransferItem.item_id == Item.id)
            .join(from_location, Transfer.from_location_id == from_location.id)
            .join(to_location, Transfer.to_location_id == to_location.id)
            .filter(
                TransferItem.completed_quantity > 0,
                Transfer.date_created >= start_datetime,
                Transfer.date_created <= end_datetime,
            )
        )

        if from_location_ids:
            aggregated_transfers = aggregated_transfers.filter(
                Transfer.from_location_id.in_(from_location_ids)
            )

        if to_location_ids:
            aggregated_transfers = aggregated_transfers.filter(
                Transfer.to_location_id.in_(to_location_ids)
            )

        aggregated_transfers = (
            aggregated_transfers.group_by(
                from_location.id,
                to_location.id,
                Item.id,
                from_location.name,
                to_location.name,
                Item.name,
            )
            .order_by(from_location.name, to_location.name, Item.name)
            .all()
        )

        # Process the results for display or session storage
        session["aggregated_transfers"] = [
            {
                "from_location_name": result[0],
                "to_location_name": result[1],
                "item_name": result[2],
                "total_quantity": result[3],
            }
            for result in aggregated_transfers
        ]

        # Store start and end date/time in session for use in the report
        session["report_start_datetime"] = start_datetime.strftime(
            "%Y-%m-%d %H:%M"
        )
        session["report_end_datetime"] = end_datetime.strftime(
            "%Y-%m-%d %H:%M"
        )

        session["report_from_locations"] = [
            location_lookup.get(location_id)
            for location_id in from_location_ids
            if location_lookup.get(location_id)
        ]
        session["report_to_locations"] = [
            location_lookup.get(location_id)
            for location_id in to_location_ids
            if location_lookup.get(location_id)
        ]

        flash("Transfer report generated successfully.", "success")
        return redirect(url_for("transfer.view_report"))

    return render_template("transfers/generate_report.html", form=form)


@transfer.route("/transfers/report")
@login_required
def view_report():
    """Display the previously generated transfer report."""
    aggregated_transfers = session.get("aggregated_transfers", [])
    return render_template(
        "transfers/view_report.html",
        aggregated_transfers=aggregated_transfers,
        from_locations=session.get("report_from_locations", []),
        to_locations=session.get("report_to_locations", []),
    )
