from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    url_for,
    jsonify,
)
from flask_login import login_required

from app import db
from app.forms import CustomerForm, DeleteForm
from app.models import Customer
from app.utils.activity import log_activity
from app.utils.pagination import build_pagination_args, get_per_page
from sqlalchemy import func

customer = Blueprint("customer", __name__)


@customer.route("/customers")
@login_required
def view_customers():
    """Display all customers."""
    page = request.args.get("page", 1, type=int)
    name_query = request.args.get("name_query", "")
    match_mode = request.args.get("match_mode", "contains")
    gst_exempt = request.args.get("gst_exempt", "all")
    pst_exempt = request.args.get("pst_exempt", "all")

    query = Customer.query.filter_by(archived=False)
    if name_query:
        full_name = func.concat(Customer.first_name, " ", Customer.last_name)
        if match_mode == "exact":
            query = query.filter(full_name == name_query)
        elif match_mode == "startswith":
            query = query.filter(full_name.like(f"{name_query}%"))
        elif match_mode == "not_contains":
            query = query.filter(full_name.notlike(f"%{name_query}%"))
        else:
            query = query.filter(full_name.like(f"%{name_query}%"))
    if gst_exempt == "yes":
        query = query.filter(Customer.gst_exempt.is_(True))
    elif gst_exempt == "no":
        query = query.filter(Customer.gst_exempt.is_(False))
    if pst_exempt == "yes":
        query = query.filter(Customer.pst_exempt.is_(True))
    elif pst_exempt == "no":
        query = query.filter(Customer.pst_exempt.is_(False))

    per_page = get_per_page()
    customers = query.paginate(page=page, per_page=per_page)
    delete_form = DeleteForm()
    form = CustomerForm()
    return render_template(
        "customers/view_customers.html",
        customers=customers,
        delete_form=delete_form,
        form=form,
        name_query=name_query,
        match_mode=match_mode,
        gst_exempt=gst_exempt,
        pst_exempt=pst_exempt,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


@customer.route("/customers/create", methods=["GET", "POST"])
@login_required
def create_customer():
    """Add a customer record."""
    form = CustomerForm()
    if form.validate_on_submit():
        customer = Customer(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            # Checkbox checked means charge tax, so exemption is the inverse
            gst_exempt=not form.gst_exempt.data,
            pst_exempt=not form.pst_exempt.data,
        )
        db.session.add(customer)
        db.session.commit()
        log_activity(f"Created customer {customer.id}")
        flash("Customer created successfully!", "success")
        return redirect(url_for("customer.view_customers"))
    return render_template(
        "customers/customer_form_page.html", form=form, title="Create Customer"
    )


@customer.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    """Edit customer details."""
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        abort(404)
    form = CustomerForm()

    if form.validate_on_submit():
        customer.first_name = form.first_name.data
        customer.last_name = form.last_name.data
        # Store exemptions as the inverse of the checkbox state
        customer.gst_exempt = not form.gst_exempt.data
        customer.pst_exempt = not form.pst_exempt.data
        db.session.commit()
        log_activity(f"Edited customer {customer.id}")
        flash("Customer updated successfully!", "success")
        return redirect(url_for("customer.view_customers"))

    elif request.method == "GET":
        form.first_name.data = customer.first_name
        form.last_name.data = customer.last_name
        # Invert stored values so the checkbox represents charging tax
        form.gst_exempt.data = not customer.gst_exempt
        form.pst_exempt.data = not customer.pst_exempt

    return render_template(
        "customers/customer_form_page.html", form=form, title="Edit Customer"
    )


@customer.route("/customers/create-modal", methods=["POST"])
@login_required
def create_customer_modal():
    """Create a customer via AJAX and return JSON."""
    form = CustomerForm()
    if form.validate_on_submit():
        customer = Customer(
            first_name=form.first_name.data,
            last_name=form.last_name.data,
            # Checkbox checked means charge tax, so exemption is the inverse
            gst_exempt=not form.gst_exempt.data,
            pst_exempt=not form.pst_exempt.data,
        )
        db.session.add(customer)
        db.session.commit()
        log_activity(f"Created customer {customer.id}")
        delete_form = DeleteForm()
        return jsonify(
            {
                "success": True,
                "customer": {
                    "id": customer.id,
                    "first_name": customer.first_name,
                    "last_name": customer.last_name,
                    "gst_exempt": customer.gst_exempt,
                    "pst_exempt": customer.pst_exempt,
                },
                "delete_csrf_token": delete_form.csrf_token.current_token,
            }
        )
    return jsonify({"success": False, "errors": form.errors}), 400


@customer.route("/customers/<int:customer_id>/delete", methods=["POST"])
@login_required
def delete_customer(customer_id):
    """Delete a customer."""
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)
    customer = db.session.get(Customer, customer_id)
    if customer is None:
        abort(404)
    customer.archived = True
    db.session.commit()
    log_activity(f"Archived customer {customer.id}")
    flash("Customer archived successfully!", "success")
    return redirect(url_for("customer.view_customers"))
