from flask import Blueprint, render_template, request
from flask_login import current_user, login_required

from app.forms import ConfirmForm
from app.services.dashboard_metrics import dashboard_context

main = Blueprint("main", __name__)


@main.route("/")
@login_required
def home():
    """Render the dashboard with aggregated context."""

    from app.forms import TransferForm

    activity_interval = request.args.get("activity_interval")
    context = dashboard_context(activity_interval=activity_interval)
    form = TransferForm()
    add_form = TransferForm(prefix="add")
    edit_form = TransferForm(prefix="edit")
    confirm_form = ConfirmForm()

    return render_template(
        "dashboard.html",
        user=current_user,
        context=context,
        form=form,
        add_form=add_form,
        edit_form=edit_form,
        confirm_form=confirm_form,
    )
