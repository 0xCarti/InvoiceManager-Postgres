from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app import db
from app.forms import BulletinPostForm, CSRFOnlyForm, CommunicationMessageForm
from app.models import Communication, CommunicationRecipient
from app.services.communication_service import (
    active_bulletin_receipt_for_user,
    active_bulletin_receipts_for_user,
    active_bulletin_receipts_query_for_user,
    build_bulletin_audience_snapshot,
    can_manage_bulletin,
    communication_scope_departments,
    communication_scope_users,
    resolve_communication_recipients,
    sync_dynamic_bulletin_receipts_for_user,
    sync_dynamic_bulletin_recipients,
    visible_message_history,
)
from app.utils.activity import log_activity
from app.utils.dashboard_bulletins import (
    load_saved_dashboard_bulletin_ids,
    set_saved_dashboard_bulletin_state,
)

communication = Blueprint("communication", __name__)
COMMUNICATION_BULLETIN_PAGE_SIZE = 10


def _configure_compose_form_choices(form, scoped_users, scoped_departments) -> None:
    form.recipient_user_ids.choices = [
        (user.id, user.display_label) for user in scoped_users
    ]
    form.department_id.choices = [(0, "Select a department")] + [
        (department.id, department.name) for department in scoped_departments
    ]


def _attach_audience_error(form, message: str) -> None:
    audience = form.audience.data
    if audience == Communication.AUDIENCE_DEPARTMENT:
        form.department_id.errors.append(message)
        return
    if audience == Communication.AUDIENCE_USERS:
        form.recipient_user_ids.errors.append(message)
        return
    form.audience.errors.append(message)


def _create_communication(
    *,
    kind: str,
    audience: str,
    subject: str,
    body: str,
    department_id: int | None,
    recipients,
    audience_snapshot: dict[str, object] | None = None,
) -> Communication:
    item = Communication(
        kind=kind,
        sender=current_user,
        audience_type=audience,
        audience_snapshot=audience_snapshot,
        subject=subject,
        body=body,
        department_id=department_id,
        pinned=(kind == Communication.KIND_BULLETIN),
    )
    item.recipients = [
        CommunicationRecipient(user=user)
        for user in recipients
    ]
    db.session.add(item)
    return item


def _coerce_positive_int(value, *, default: int = 1) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, normalized)


def _communication_center_redirect(
    *,
    bulletin_page=None,
    bulletin_id=None,
) -> str:
    params = {}
    normalized_page = _coerce_positive_int(bulletin_page, default=1)
    if normalized_page > 1:
        params["bulletin_page"] = normalized_page
    normalized_bulletin_id = _coerce_positive_int(bulletin_id, default=0)
    if normalized_bulletin_id > 0:
        params["bulletin_id"] = normalized_bulletin_id
    return url_for("communication.center", **params)


def _build_bulletin_read_summary(receipt: CommunicationRecipient) -> dict[str, object]:
    recipients = list(getattr(receipt.communication, "recipients", []) or [])
    read_receipts = sorted(
        [item for item in recipients if item.read_at is not None],
        key=lambda item: (item.read_at or datetime.min, item.user.display_label),
        reverse=True,
    )
    unread_receipts = sorted(
        [item for item in recipients if item.read_at is None],
        key=lambda item: item.user.display_label,
    )
    return {
        "total_count": len(recipients),
        "read_count": len(read_receipts),
        "unread_count": len(unread_receipts),
        "read_receipts": read_receipts,
        "unread_receipts": unread_receipts,
    }


@communication.route("/communications", methods=["GET", "POST"])
@login_required
def center():
    if not current_user.has_any_permission(
        "communications.view",
        "communications.view_history",
        "communications.send_direct",
        "communications.send_broadcast",
        "communications.manage_bulletin",
        "communications.view_bulletin_receipts",
    ):
        abort(403)

    scoped_users = communication_scope_users(current_user)
    scoped_departments = communication_scope_departments(current_user)

    message_form = CommunicationMessageForm(prefix="message")
    bulletin_form = BulletinPostForm(prefix="bulletin")
    action_form = CSRFOnlyForm(prefix="communication")
    _configure_compose_form_choices(message_form, scoped_users, scoped_departments)
    _configure_compose_form_choices(bulletin_form, scoped_users, scoped_departments)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "send_message":
            if not current_user.has_any_permission(
                "communications.send_direct",
                "communications.send_broadcast",
            ):
                abort(403)
            if message_form.validate_on_submit():
                selected_user_ids = message_form.recipient_user_ids.data or []
                needs_broadcast_permission = (
                    message_form.audience.data != Communication.AUDIENCE_USERS
                    or len(selected_user_ids) != 1
                )
                if needs_broadcast_permission and not current_user.has_permission(
                    "communications.send_broadcast"
                ):
                    abort(403)
                try:
                    recipients = resolve_communication_recipients(
                        current_user,
                        audience=message_form.audience.data,
                        user_ids=selected_user_ids,
                        department_id=message_form.department_id.data or None,
                        include_actor=False,
                    )
                except (PermissionError, ValueError) as exc:
                    _attach_audience_error(message_form, str(exc))
                else:
                    message = _create_communication(
                        kind=Communication.KIND_MESSAGE,
                        audience=message_form.audience.data,
                        subject=(message_form.subject.data or "").strip(),
                        body=(message_form.body.data or "").strip(),
                        department_id=(
                            message_form.department_id.data
                            if message_form.audience.data
                            == Communication.AUDIENCE_DEPARTMENT
                            else None
                        ),
                        recipients=recipients,
                    )
                    db.session.commit()
                    log_activity(
                        f"Sent communication message {message.id} to {len(recipients)} user(s)"
                    )
                    flash(
                        f"Message sent to {len(recipients)} user(s).",
                        "success",
                    )
                    return redirect(url_for("communication.center"))

        elif action == "post_bulletin":
            if not current_user.has_permission("communications.manage_bulletin"):
                abort(403)
            if bulletin_form.validate_on_submit():
                try:
                    recipients = resolve_communication_recipients(
                        current_user,
                        audience=bulletin_form.audience.data,
                        user_ids=bulletin_form.recipient_user_ids.data or [],
                        department_id=bulletin_form.department_id.data or None,
                        include_actor=True,
                    )
                except (PermissionError, ValueError) as exc:
                    _attach_audience_error(bulletin_form, str(exc))
                else:
                    bulletin = _create_communication(
                        kind=Communication.KIND_BULLETIN,
                        audience=bulletin_form.audience.data,
                        audience_snapshot=build_bulletin_audience_snapshot(
                            current_user,
                            audience=bulletin_form.audience.data,
                        ),
                        subject=(bulletin_form.subject.data or "").strip(),
                        body=(bulletin_form.body.data or "").strip(),
                        department_id=(
                            bulletin_form.department_id.data
                            if bulletin_form.audience.data
                            == Communication.AUDIENCE_DEPARTMENT
                            else None
                        ),
                        recipients=recipients,
                    )
                    db.session.commit()
                    log_activity(
                        f"Posted bulletin {bulletin.id} for {len(recipients)} user(s)"
                    )
                    flash(
                        f"Bulletin posted for {len(recipients)} user(s).",
                        "success",
                    )
                    return redirect(url_for("communication.center"))

        elif action == "mark_read":
            receipt = db.session.get(
                CommunicationRecipient,
                int(request.form.get("receipt_id") or 0),
            )
            if receipt is None or receipt.user_id != current_user.id:
                abort(404)
            receipt.mark_read()
            db.session.commit()
            return redirect(
                _communication_center_redirect(
                    bulletin_page=request.form.get("bulletin_page"),
                    bulletin_id=(
                        request.form.get("communication_id")
                        or request.form.get("bulletin_id")
                    ),
                )
            )

        elif action == "toggle_dashboard_bulletin":
            if not current_user.can_access_endpoint("main.home", "GET"):
                abort(403)
            communication_id = int(request.form.get("communication_id") or 0)
            receipt = active_bulletin_receipt_for_user(current_user, communication_id)
            if receipt is None:
                abort(404)
            try:
                saved_ids = set_saved_dashboard_bulletin_state(
                    current_user,
                    communication_id,
                    saved=request.form.get("save_on_dashboard") == "1",
                )
            except ValueError as exc:
                flash(str(exc), "danger")
            else:
                if communication_id in saved_ids:
                    flash("Bulletin saved to your dashboard.", "success")
                else:
                    flash("Bulletin removed from your dashboard.", "success")
            return redirect(
                _communication_center_redirect(
                    bulletin_page=request.form.get("bulletin_page"),
                    bulletin_id=communication_id,
                )
            )

        elif action == "deactivate_bulletin":
            if not current_user.has_permission("communications.manage_bulletin"):
                abort(403)
            bulletin = db.session.get(
                Communication,
                int(request.form.get("communication_id") or 0),
            )
            if bulletin is None or not bulletin.is_bulletin:
                abort(404)
            if not can_manage_bulletin(current_user, bulletin.sender_id):
                abort(403)
            bulletin.active = False
            bulletin.pinned = False
            db.session.commit()
            log_activity(f"Archived bulletin {bulletin.id}")
            flash("Bulletin archived.", "success")
            return redirect(
                _communication_center_redirect(
                    bulletin_page=request.form.get("bulletin_page"),
                )
            )

    receipt_options = (
        selectinload(CommunicationRecipient.communication)
        .selectinload(Communication.sender),
        selectinload(CommunicationRecipient.communication)
        .selectinload(Communication.department),
    )
    inbox_receipts = (
        CommunicationRecipient.query.options(*receipt_options)
        .join(Communication, CommunicationRecipient.communication_id == Communication.id)
        .filter(
            CommunicationRecipient.user_id == current_user.id,
            Communication.kind == Communication.KIND_MESSAGE,
        )
        .all()
    )
    inbox_receipts = sorted(
        inbox_receipts,
        key=lambda receipt: (
            receipt.read_at is None,
            getattr(receipt.communication, "created_at", datetime.min),
        ),
        reverse=True,
    )

    can_view_bulletin_receipts = current_user.has_permission(
        "communications.view_bulletin_receipts"
    )
    sync_dynamic_bulletin_receipts_for_user(current_user)
    can_save_dashboard_bulletins = current_user.can_access_endpoint("main.home", "GET")
    saved_dashboard_bulletin_ids = set(
        load_saved_dashboard_bulletin_ids(current_user)
        if can_save_dashboard_bulletins
        else []
    )
    bulletin_page = _coerce_positive_int(
        request.args.get("bulletin_page"),
        default=1,
    )
    bulletin_query = active_bulletin_receipts_query_for_user(current_user)
    bulletin_receipts_pagination = bulletin_query.paginate(
        page=bulletin_page,
        per_page=COMMUNICATION_BULLETIN_PAGE_SIZE,
        error_out=False,
    )
    if (
        bulletin_receipts_pagination.pages
        and bulletin_page > bulletin_receipts_pagination.pages
    ):
        bulletin_page = bulletin_receipts_pagination.pages
        bulletin_receipts_pagination = bulletin_query.paginate(
            page=bulletin_page,
            per_page=COMMUNICATION_BULLETIN_PAGE_SIZE,
            error_out=False,
        )
    bulletin_receipts = bulletin_receipts_pagination.items
    selected_bulletin_id = _coerce_positive_int(
        request.args.get("bulletin_id"),
        default=0,
    )
    if selected_bulletin_id:
        if can_view_bulletin_receipts:
            sync_dynamic_bulletin_recipients(selected_bulletin_id)
        selected_bulletin_receipt = active_bulletin_receipt_for_user(
            current_user,
            selected_bulletin_id,
            include_recipient_users=can_view_bulletin_receipts,
        )
    elif bulletin_receipts:
        if can_view_bulletin_receipts:
            sync_dynamic_bulletin_recipients(bulletin_receipts[0].communication_id)
        selected_bulletin_receipt = active_bulletin_receipt_for_user(
            current_user,
            bulletin_receipts[0].communication_id,
            include_recipient_users=can_view_bulletin_receipts,
        )
    else:
        selected_bulletin_receipt = None
    bulletin_read_summary = (
        _build_bulletin_read_summary(selected_bulletin_receipt)
        if selected_bulletin_receipt is not None and can_view_bulletin_receipts
        else None
    )
    bulletin_unread_count = (
        active_bulletin_receipts_query_for_user(current_user)
        .order_by(None)
        .filter(CommunicationRecipient.read_at.is_(None))
        .count()
    )
    manageable_bulletin_ids = {
        receipt.communication.id
        for receipt in active_bulletin_receipts_for_user(current_user)
        if can_manage_bulletin(current_user, receipt.communication.sender_id)
    }

    sent_items = []
    if current_user.has_any_permission(
        "communications.send_direct",
        "communications.send_broadcast",
        "communications.manage_bulletin",
    ):
        sent_items = (
            Communication.query.options(
                selectinload(Communication.department),
                selectinload(Communication.recipients),
            )
            .filter_by(sender_id=current_user.id)
            .order_by(Communication.created_at.desc())
            .limit(10)
            .all()
        )

    scoped_message_history = []
    if current_user.has_permission("communications.view_history"):
        scoped_message_history = visible_message_history(current_user)

    return render_template(
        "communications/center.html",
        message_form=message_form,
        bulletin_form=bulletin_form,
        action_form=action_form,
        inbox_receipts=inbox_receipts,
        bulletin_receipts=bulletin_receipts,
        bulletin_receipts_pagination=bulletin_receipts_pagination,
        bulletin_page=bulletin_page,
        bulletin_unread_count=bulletin_unread_count,
        selected_bulletin_receipt=selected_bulletin_receipt,
        selected_bulletin_read_summary=bulletin_read_summary,
        sent_items=sent_items,
        can_send_direct=current_user.has_any_permission(
            "communications.send_direct",
            "communications.send_broadcast",
        ),
        can_send_broadcast=current_user.has_permission("communications.send_broadcast"),
        can_manage_bulletins=current_user.has_permission(
            "communications.manage_bulletin"
        ),
        can_view_message_history=current_user.has_permission(
            "communications.view_history"
        ),
        can_view_bulletin_receipts=can_view_bulletin_receipts,
        can_save_dashboard_bulletins=can_save_dashboard_bulletins,
        saved_dashboard_bulletin_ids=saved_dashboard_bulletin_ids,
        scoped_message_history=scoped_message_history,
        manageable_bulletin_ids=manageable_bulletin_ids,
    )
