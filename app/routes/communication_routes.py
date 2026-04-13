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
    active_bulletin_receipts_query_for_user,
    build_bulletin_audience_snapshot,
    can_manage_bulletin,
    communication_scope_departments,
    communication_scope_users,
    message_receipt_for_user,
    message_receipts_query_for_user,
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
COMMUNICATION_MESSAGE_PAGE_SIZE = 20
COMMUNICATION_ACCESS_PERMISSIONS = (
    "communications.view",
    "communications.view_history",
    "communications.send_direct",
    "communications.send_broadcast",
    "communications.manage_bulletin",
    "communications.view_bulletin_receipts",
)


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
    item.recipients = [CommunicationRecipient(user=user) for user in recipients]
    db.session.add(item)
    return item


def _coerce_positive_int(
    value,
    *,
    default: int = 1,
    minimum: int = 1,
) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, normalized)


def _ensure_communication_access() -> None:
    if not current_user.has_any_permission(*COMMUNICATION_ACCESS_PERMISSIONS):
        abort(403)


def _communication_center_redirect(*, bulletin_page=None) -> str:
    params = {}
    normalized_page = _coerce_positive_int(
        bulletin_page,
        default=1,
        minimum=1,
    )
    if normalized_page > 1:
        params["bulletin_page"] = normalized_page
    return url_for("communication.center", **params)


def _bulletin_detail_redirect(*, communication_id, bulletin_page=None) -> str:
    params = {
        "communication_id": _coerce_positive_int(
            communication_id,
            default=1,
            minimum=1,
        )
    }
    normalized_page = _coerce_positive_int(
        bulletin_page,
        default=1,
        minimum=1,
    )
    if normalized_page > 1:
        params["bulletin_page"] = normalized_page
    return url_for("communication.bulletin_detail", **params)


def _redirect_after_bulletin_action(
    *,
    communication_id=None,
    bulletin_page=None,
) -> str:
    if request.form.get("return_view") == "bulletin_detail":
        normalized_communication_id = _coerce_positive_int(
            communication_id,
            default=0,
            minimum=0,
        )
        if normalized_communication_id > 0:
            return _bulletin_detail_redirect(
                communication_id=normalized_communication_id,
                bulletin_page=bulletin_page,
            )
    return _communication_center_redirect(bulletin_page=bulletin_page)


def _normalize_message_mailbox(value) -> str:
    return "archived" if (value or "").strip().lower() == "archived" else "inbox"


def _message_index_redirect(*, mailbox="inbox", message_page=None) -> str:
    params = {}
    normalized_mailbox = _normalize_message_mailbox(mailbox)
    if normalized_mailbox == "archived":
        params["mailbox"] = normalized_mailbox
    normalized_page = _coerce_positive_int(
        message_page,
        default=1,
        minimum=1,
    )
    if normalized_page > 1:
        params["message_page"] = normalized_page
    return url_for("communication.messages", **params)


def _message_detail_redirect(*, receipt_id, mailbox="inbox", message_page=None) -> str:
    params = {
        "receipt_id": _coerce_positive_int(
            receipt_id,
            default=1,
            minimum=1,
        )
    }
    normalized_mailbox = _normalize_message_mailbox(mailbox)
    if normalized_mailbox == "archived":
        params["mailbox"] = normalized_mailbox
    normalized_page = _coerce_positive_int(
        message_page,
        default=1,
        minimum=1,
    )
    if normalized_page > 1:
        params["message_page"] = normalized_page
    return url_for("communication.message_detail", **params)


def _redirect_after_message_action(
    *,
    receipt_id=None,
    mailbox="inbox",
    message_page=None,
) -> str:
    if request.form.get("return_view") == "message_detail":
        normalized_receipt_id = _coerce_positive_int(
            receipt_id,
            default=0,
            minimum=0,
        )
        if normalized_receipt_id > 0:
            return _message_detail_redirect(
                receipt_id=normalized_receipt_id,
                mailbox=mailbox,
                message_page=message_page,
            )
    return _message_index_redirect(
        mailbox=mailbox,
        message_page=message_page,
    )


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


def _submit_message(message_form: CommunicationMessageForm) -> bool:
    if not current_user.has_any_permission(
        "communications.send_direct",
        "communications.send_broadcast",
    ):
        abort(403)
    if not message_form.validate_on_submit():
        return False

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
        return False

    message = _create_communication(
        kind=Communication.KIND_MESSAGE,
        audience=message_form.audience.data,
        subject=(message_form.subject.data or "").strip(),
        body=(message_form.body.data or "").strip(),
        department_id=(
            message_form.department_id.data
            if message_form.audience.data == Communication.AUDIENCE_DEPARTMENT
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
    return True


def _submit_bulletin(bulletin_form: BulletinPostForm) -> bool:
    if not current_user.has_permission("communications.manage_bulletin"):
        abort(403)
    if not bulletin_form.validate_on_submit():
        return False

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
        return False

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
            if bulletin_form.audience.data == Communication.AUDIENCE_DEPARTMENT
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
    return True


@communication.route("/communications", methods=["GET", "POST"])
@login_required
def center():
    _ensure_communication_access()

    legacy_bulletin_id = _coerce_positive_int(
        request.args.get("bulletin_id"),
        default=0,
        minimum=0,
    )
    if request.method == "GET" and legacy_bulletin_id:
        return redirect(
            _bulletin_detail_redirect(
                communication_id=legacy_bulletin_id,
                bulletin_page=request.args.get("bulletin_page"),
            )
        )

    scoped_users = communication_scope_users(current_user)
    scoped_departments = communication_scope_departments(current_user)

    message_form = CommunicationMessageForm(prefix="message")
    bulletin_form = BulletinPostForm(prefix="bulletin")
    action_form = CSRFOnlyForm(prefix="communication")
    _configure_compose_form_choices(message_form, scoped_users, scoped_departments)
    _configure_compose_form_choices(bulletin_form, scoped_users, scoped_departments)

    open_modal_id = None

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "send_message":
            if _submit_message(message_form):
                return redirect(url_for("communication.center"))
            else:
                open_modal_id = "sendMessageModal"

        elif action == "post_bulletin":
            if _submit_bulletin(bulletin_form):
                return redirect(url_for("communication.center"))
            else:
                open_modal_id = "postBulletinModal"

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
                _redirect_after_bulletin_action(
                    communication_id=(
                        request.form.get("communication_id")
                        or request.form.get("bulletin_id")
                    ),
                    bulletin_page=request.form.get("bulletin_page"),
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
                _redirect_after_bulletin_action(
                    communication_id=communication_id,
                    bulletin_page=request.form.get("bulletin_page"),
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

    inbox_receipts = message_receipts_query_for_user(current_user, archived=False).all()

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
        minimum=1,
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

    bulletin_unread_count = (
        active_bulletin_receipts_query_for_user(current_user)
        .order_by(None)
        .filter(CommunicationRecipient.read_at.is_(None))
        .count()
    )

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
        action_form=action_form,
        bulletin_form=bulletin_form,
        bulletin_page=bulletin_page,
        bulletin_receipts_pagination=bulletin_receipts_pagination,
        bulletin_unread_count=bulletin_unread_count,
        can_manage_bulletins=current_user.has_permission(
            "communications.manage_bulletin"
        ),
        can_save_dashboard_bulletins=can_save_dashboard_bulletins,
        can_send_direct=current_user.has_any_permission(
            "communications.send_direct",
            "communications.send_broadcast",
        ),
        can_view_message_history=current_user.has_permission(
            "communications.view_history"
        ),
        inbox_receipts=inbox_receipts,
        message_form=message_form,
        open_modal_id=open_modal_id,
        saved_dashboard_bulletin_ids=saved_dashboard_bulletin_ids,
        scoped_message_history=scoped_message_history,
        sent_items=sent_items,
    )


@communication.route("/communications/messages", methods=["GET", "POST"])
@login_required
def messages():
    _ensure_communication_access()

    scoped_users = communication_scope_users(current_user)
    scoped_departments = communication_scope_departments(current_user)
    message_form = CommunicationMessageForm(prefix="message")
    action_form = CSRFOnlyForm(prefix="message_list")
    _configure_compose_form_choices(message_form, scoped_users, scoped_departments)

    can_send_direct = current_user.has_any_permission(
        "communications.send_direct",
        "communications.send_broadcast",
    )
    mailbox = _normalize_message_mailbox(
        request.form.get("mailbox") or request.args.get("mailbox")
    )
    open_modal_id = (
        "messagesSendMessageModal"
        if request.method == "GET"
        and request.args.get("compose") == "1"
        and can_send_direct
        else None
    )

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()

        if action == "send_message":
            if _submit_message(message_form):
                return redirect(_message_index_redirect(mailbox="inbox"))
            open_modal_id = "messagesSendMessageModal"

        elif action == "mark_read_message":
            receipt = message_receipt_for_user(
                current_user,
                int(request.form.get("receipt_id") or 0),
                include_archived=True,
            )
            if receipt is None:
                abort(404)
            receipt.mark_read()
            db.session.commit()
            return redirect(
                _redirect_after_message_action(
                    receipt_id=receipt.id,
                    mailbox=request.form.get("mailbox"),
                    message_page=request.form.get("message_page"),
                )
            )

        elif action == "archive_message":
            receipt = message_receipt_for_user(
                current_user,
                int(request.form.get("receipt_id") or 0),
                include_archived=False,
            )
            if receipt is None:
                abort(404)
            receipt.archive()
            db.session.commit()
            flash("Message archived.", "success")
            return redirect(
                _redirect_after_message_action(
                    receipt_id=receipt.id,
                    mailbox="archived",
                    message_page=request.form.get("message_page"),
                )
            )

        elif action == "restore_message":
            receipt = message_receipt_for_user(
                current_user,
                int(request.form.get("receipt_id") or 0),
                include_archived=True,
            )
            if receipt is None or receipt.archived_at is None:
                abort(404)
            receipt.restore()
            db.session.commit()
            flash("Message moved back to inbox.", "success")
            return redirect(
                _redirect_after_message_action(
                    receipt_id=receipt.id,
                    mailbox="inbox",
                    message_page=request.form.get("message_page"),
                )
            )

        elif action == "delete_message":
            receipt = message_receipt_for_user(
                current_user,
                int(request.form.get("receipt_id") or 0),
                include_archived=True,
            )
            if receipt is None:
                abort(404)
            receipt.delete_for_user()
            db.session.commit()
            flash("Message deleted from your mailbox.", "success")
            return redirect(
                _message_index_redirect(
                    mailbox=request.form.get("mailbox"),
                    message_page=request.form.get("message_page"),
                )
            )

    message_page = _coerce_positive_int(
        request.args.get("message_page"),
        default=1,
        minimum=1,
    )
    archived_mailbox = mailbox == "archived"
    message_query = message_receipts_query_for_user(
        current_user,
        archived=archived_mailbox,
    )
    message_receipts_pagination = message_query.paginate(
        page=message_page,
        per_page=COMMUNICATION_MESSAGE_PAGE_SIZE,
        error_out=False,
    )
    if (
        message_receipts_pagination.pages
        and message_page > message_receipts_pagination.pages
    ):
        message_page = message_receipts_pagination.pages
        message_receipts_pagination = message_query.paginate(
            page=message_page,
            per_page=COMMUNICATION_MESSAGE_PAGE_SIZE,
            error_out=False,
        )

    inbox_count = (
        message_receipts_query_for_user(current_user, archived=False)
        .order_by(None)
        .count()
    )
    archived_count = (
        message_receipts_query_for_user(current_user, archived=True)
        .order_by(None)
        .count()
    )
    unread_count = (
        message_receipts_query_for_user(current_user, archived=False)
        .order_by(None)
        .filter(CommunicationRecipient.read_at.is_(None))
        .count()
    )

    return render_template(
        "communications/messages.html",
        action_form=action_form,
        active_mailbox=mailbox,
        archived_count=archived_count,
        can_send_direct=can_send_direct,
        inbox_count=inbox_count,
        message_form=message_form,
        message_page=message_page,
        message_receipts_pagination=message_receipts_pagination,
        open_modal_id=open_modal_id,
        unread_count=unread_count,
    )


@communication.route("/communications/messages/<int:receipt_id>", methods=["GET"])
@login_required
def message_detail(receipt_id: int):
    _ensure_communication_access()

    mailbox = _normalize_message_mailbox(request.args.get("mailbox"))
    message_page = _coerce_positive_int(
        request.args.get("message_page"),
        default=1,
        minimum=1,
    )
    receipt = message_receipt_for_user(
        current_user,
        receipt_id,
        include_archived=True,
    )
    if receipt is None:
        abort(404)

    if receipt.read_at is None:
        receipt.mark_read()
        db.session.commit()

    action_form = CSRFOnlyForm(prefix="message_list")

    return render_template(
        "communications/message_detail.html",
        action_form=action_form,
        back_to_messages_url=_message_index_redirect(
            mailbox=mailbox,
            message_page=message_page,
        ),
        can_send_direct=current_user.has_any_permission(
            "communications.send_direct",
            "communications.send_broadcast",
        ),
        mailbox=mailbox,
        message_page=message_page,
        receipt=receipt,
    )


@communication.route("/communications/bulletins/<int:communication_id>", methods=["GET"])
@login_required
def bulletin_detail(communication_id: int):
    _ensure_communication_access()

    can_view_bulletin_receipts = current_user.has_permission(
        "communications.view_bulletin_receipts"
    )
    if can_view_bulletin_receipts:
        sync_dynamic_bulletin_recipients(communication_id)

    receipt = active_bulletin_receipt_for_user(
        current_user,
        communication_id,
        include_recipient_users=can_view_bulletin_receipts,
    )
    if receipt is None:
        abort(404)

    action_form = CSRFOnlyForm(prefix="communication")
    bulletin_page = _coerce_positive_int(
        request.args.get("bulletin_page"),
        default=1,
        minimum=1,
    )
    can_save_dashboard_bulletins = current_user.can_access_endpoint("main.home", "GET")
    saved_dashboard_bulletin_ids = set(
        load_saved_dashboard_bulletin_ids(current_user)
        if can_save_dashboard_bulletins
        else []
    )
    bulletin_read_summary = (
        _build_bulletin_read_summary(receipt) if can_view_bulletin_receipts else None
    )

    return render_template(
        "communications/bulletin_detail.html",
        action_form=action_form,
        back_to_center_url=_communication_center_redirect(bulletin_page=bulletin_page),
        bulletin_page=bulletin_page,
        bulletin_read_summary=bulletin_read_summary,
        can_manage_this_bulletin=(
            current_user.has_permission("communications.manage_bulletin")
            and can_manage_bulletin(
                current_user,
                receipt.communication.sender_id,
            )
        ),
        can_save_dashboard_bulletins=can_save_dashboard_bulletins,
        can_view_bulletin_receipts=can_view_bulletin_receipts,
        receipt=receipt,
        saved_dashboard_bulletin_ids=saved_dashboard_bulletin_ids,
    )
