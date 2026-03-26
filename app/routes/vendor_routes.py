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

from app import db
from app.forms import CustomerForm, DeleteForm
from app.models import Vendor
from app.utils.activity import log_activity
from app.utils.pagination import build_pagination_args, get_per_page

vendor = Blueprint("vendor", __name__)


@vendor.route("/vendors")
@login_required
def view_vendors():
    """Display all vendors."""
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    vendors = Vendor.query.filter_by(archived=False).paginate(
        page=page, per_page=per_page
    )
    delete_form = DeleteForm()
    return render_template(
        "vendors/view_vendors.html",
        vendors=vendors,
        delete_form=delete_form,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


@vendor.route("/vendors/create", methods=["GET", "POST"])
@login_required
def create_vendor():
    """Create a new vendor."""
    form = CustomerForm()
    if form.validate_on_submit():
        vendor = Vendor(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            # Checkbox checked means charge tax, so exemption is the inverse
            gst_exempt=not form.gst_exempt.data,
            pst_exempt=not form.pst_exempt.data,
        )
        db.session.add(vendor)
        db.session.commit()
        log_activity(f"Created vendor {vendor.id}")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            delete_form = DeleteForm()
            row_html = render_template(
                "vendors/_vendor_row.html", vendor=vendor, delete_form=delete_form
            )
            return jsonify({"success": True, "row_html": row_html})
        flash("Vendor created successfully!", "success")
        return redirect(url_for("vendor.view_vendors"))
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        if request.method == "POST":
            form_html = render_template("vendors/vendor_form.html", form=form)
            return jsonify({"success": False, "form_html": form_html})
        return render_template("vendors/vendor_form.html", form=form)
    return render_template(
        "vendors/vendor_form_page.html", form=form, title="Create Vendor"
    )


@vendor.route("/vendors/<int:vendor_id>/edit", methods=["GET", "POST"])
@login_required
def edit_vendor(vendor_id):
    """Edit vendor information."""
    vendor = db.session.get(Vendor, vendor_id)
    if vendor is None:
        abort(404)
    form = CustomerForm()

    if form.validate_on_submit():
        vendor.first_name = form.first_name.data
        vendor.last_name = form.last_name.data
        # Store exemptions as the inverse of the checkbox state
        vendor.gst_exempt = not form.gst_exempt.data
        vendor.pst_exempt = not form.pst_exempt.data
        db.session.commit()
        log_activity(f"Edited vendor {vendor.id}")
        flash("Vendor updated successfully!", "success")
        return redirect(url_for("vendor.view_vendors"))

    elif request.method == "GET":
        form.first_name.data = vendor.first_name
        form.last_name.data = vendor.last_name
        # Invert stored values so the checkbox represents charging tax
        form.gst_exempt.data = not vendor.gst_exempt
        form.pst_exempt.data = not vendor.pst_exempt

    return render_template(
        "vendors/vendor_form_page.html", form=form, title="Edit Vendor"
    )


@vendor.route("/vendors/<int:vendor_id>/delete", methods=["POST"])
@login_required
def delete_vendor(vendor_id):
    """Remove a vendor from the system."""
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    vendor = db.session.get(Vendor, vendor_id)
    if vendor is None:
        abort(404)
    vendor.archived = True
    db.session.commit()
    log_activity(f"Archived vendor {vendor.id}")
    flash("Vendor archived successfully!", "success")
    return redirect(url_for("vendor.view_vendors"))
