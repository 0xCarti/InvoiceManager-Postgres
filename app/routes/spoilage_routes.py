"""Routes for handling spoilage tracking."""

# flake8: noqa

from datetime import datetime

from flask import Blueprint, render_template, request
from flask_login import login_required
from sqlalchemy import and_, or_

from app import db
from app.forms import SpoilageFilterForm
from app.models import (
    GLCode,
    Item,
    Location,
    LocationStandItem,
    Transfer,
    TransferItem,
)

spoilage = Blueprint("spoilage", __name__)


@spoilage.route("/spoilage", methods=["GET"])
@login_required
def view_spoilage():
    """Display spoilage items with optional filtering."""
    form = SpoilageFilterForm(meta={"csrf": False})
    form.process(request.args)

    # alias for from location
    from_location = db.aliased(Location)

    query = (
        db.session.query(
            TransferItem, Transfer, Item, from_location, LocationStandItem
        )
        .join(Transfer, TransferItem.transfer_id == Transfer.id)
        .join(Item, TransferItem.item_id == Item.id)
        .join(from_location, Transfer.from_location_id == from_location.id)
        .join(Location, Transfer.to_location_id == Location.id)
        .outerjoin(
            LocationStandItem,
            and_(
                LocationStandItem.location_id == Transfer.from_location_id,
                LocationStandItem.item_id == TransferItem.item_id,
            ),
        )
        .filter(Transfer.completed.is_(True), Location.is_spoilage.is_(True))
    )

    start_date_str = request.args.get("start_date")
    if start_date_str:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        form.start_date.data = start_date
        query = query.filter(
            Transfer.date_created
            >= datetime.combine(start_date, datetime.min.time())
        )
    end_date_str = request.args.get("end_date")
    if end_date_str:
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        form.end_date.data = end_date
        query = query.filter(
            Transfer.date_created
            <= datetime.combine(end_date, datetime.max.time())
        )
    if form.purchase_gl_code.data:
        code_id = form.purchase_gl_code.data
        query = query.filter(
            or_(
                LocationStandItem.purchase_gl_code_id == code_id,
                and_(
                    LocationStandItem.purchase_gl_code_id.is_(None),
                    Item.purchase_gl_code_id == code_id,
                ),
            )
        )
    if form.items.data:
        query = query.filter(TransferItem.item_id.in_(form.items.data))

    results = query.order_by(Transfer.date_created.desc()).all()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render_template("spoilage/_table.html", results=results)

    return render_template(
        "spoilage/view_spoilage.html", form=form, results=results
    )
