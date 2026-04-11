from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.forms import ConfirmForm
from app.services.dashboard_metrics import dashboard_context
from app.utils.dashboard_cards import (
    MAX_DASHBOARD_METABASE_CARDS,
    load_dashboard_metabase_cards,
    save_dashboard_metabase_cards,
    set_dashboard_metabase_card_visibility,
    validate_metabase_card_input,
)

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


@main.route("/metabase")
@login_required
def metabase_redirect():
    """Redirect permitted users to the configured Metabase site."""

    metabase_url = (current_app.config.get("METABASE_SITE_URL") or "").strip()
    if not metabase_url:
        flash("Metabase is not configured for this environment.", "warning")
        return redirect(url_for("main.home"))
    return redirect(metabase_url)


def _dashboard_card_redirect() -> str:
    return url_for(
        "main.home",
        activity_interval=(
            request.form.get("activity_interval")
            or request.args.get("activity_interval")
            or None
        ),
    )


def _persist_metabase_card_change(card_id: str | None = None) -> None:
    cards = load_dashboard_metabase_cards(current_user)
    visible_value = True
    if card_id is not None or "visible" in request.form:
        visible_value = "visible" in request.form

    if card_id is None and len(cards) >= MAX_DASHBOARD_METABASE_CARDS:
        raise ValueError(
            f"You can save up to {MAX_DASHBOARD_METABASE_CARDS} Metabase cards."
        )

    updated_card = validate_metabase_card_input(
        title=request.form.get("title"),
        embed_url=request.form.get("embed_url"),
        height=request.form.get("height"),
        metabase_site_url=current_app.config.get("METABASE_SITE_URL"),
        card_id=card_id,
        visible=visible_value,
    )

    if card_id is None:
        cards.append(updated_card)
    else:
        for index, existing_card in enumerate(cards):
            if existing_card["id"] != card_id:
                continue
            cards[index] = updated_card
            break
        else:
            abort(404)

    save_dashboard_metabase_cards(current_user, cards)


@main.route("/dashboard/metabase-cards", methods=["POST"])
@login_required
def add_metabase_card():
    """Store a new Metabase dashboard card for the current user."""

    try:
        _persist_metabase_card_change()
    except ValueError as exc:
        flash(str(exc), "danger")
    else:
        flash("Metabase report card added.", "success")
    return redirect(_dashboard_card_redirect())


@main.route("/dashboard/metabase-cards/<card_id>", methods=["POST"])
@login_required
def update_metabase_card(card_id):
    """Update a saved Metabase dashboard card."""

    try:
        _persist_metabase_card_change(card_id)
    except ValueError as exc:
        flash(str(exc), "danger")
    else:
        flash("Metabase report card updated.", "success")
    return redirect(_dashboard_card_redirect())


@main.route("/dashboard/metabase-cards/<card_id>/delete", methods=["POST"])
@login_required
def delete_metabase_card(card_id):
    """Delete a saved Metabase dashboard card."""

    cards = load_dashboard_metabase_cards(current_user)
    updated_cards = [card for card in cards if card["id"] != card_id]
    if len(updated_cards) == len(cards):
        abort(404)

    save_dashboard_metabase_cards(current_user, updated_cards)
    flash("Metabase report card removed.", "success")
    return redirect(_dashboard_card_redirect())


@main.route("/dashboard/metabase-cards/settings", methods=["POST"])
@login_required
def update_metabase_card_settings():
    """Update which Metabase cards are shown on the dashboard."""

    cards = load_dashboard_metabase_cards(current_user)
    if not cards:
        flash("No Metabase report cards are saved for this user.", "warning")
        return redirect(_dashboard_card_redirect())

    visible_card_ids = {
        value.strip()
        for value in request.form.getlist("visible_card_ids")
        if str(value).strip()
    }
    known_card_ids = {card["id"] for card in cards}
    set_dashboard_metabase_card_visibility(
        current_user,
        visible_card_ids & known_card_ids,
    )
    flash("Dashboard card visibility updated.", "success")
    return redirect(_dashboard_card_redirect())
