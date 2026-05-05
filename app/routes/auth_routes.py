import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import uuid
from datetime import date as date_cls, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import flask
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
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.exceptions import NotFound
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import safe_join, secure_filename

from app import limiter
from app.forms import (
    ActivityLogFilterForm,
    ChangePasswordForm,
    CSRFOnlyForm,
    ConfirmForm,
    CreateBackupForm,
    DeleteForm,
    ImportForm,
    InviteUserForm,
    LoginForm,
    NotificationForm,
    PasswordResetRequestForm,
    PermissionGroupForm,
    RestoreBackupForm,
    SetPasswordForm,
    SettingsForm,
    TimezoneForm,
    UserAccessForm,
    VendorItemAliasForm,
    UserForm,
    MAX_BACKUP_SIZE,
    PURCHASE_RECEIVE_DEPARTMENT_CONFIG,
)
from app.models import (
    ActivityLog,
    Customer,
    Event,
    EventLocation,
    EventLocationTerminalSalesSummary,
    GLCode,
    Location,
    Invoice,
    Item,
    LocationStandItem,
    PosSalesImport,
    PosSalesImportLocation,
    PosSalesImportRow,
    Permission,
    PermissionGroup,
    ProductRecipeItem,
    Product,
    Setting,
    TerminalSale,
    TerminalSaleLocationAlias,
    TerminalSaleProductAlias,
    VendorItemAlias,
    Transfer,
    User,
    Vendor,
    db,
)
from app.utils import send_email
from app.utils.email import SMTPConfigurationError
from app.utils.activity import log_activity
from app.utils.backup import (
    RestoreBackupError,
    UNIT_SECONDS,
    create_backup,
    restore_backup,
    start_auto_backup_thread,
    validate_backup_file_compatibility,
)
from app.utils.imports import (
    _import_csv,
    _import_items,
    _import_locations,
    _import_products,
)
from app.services.purchase_imports import (
    normalize_vendor_alias_text,
    update_or_create_vendor_alias,
)
from app.services.notification_service import (
    notification_preference_input_id,
    notification_preference_input_name,
    notify_users_for_event,
    operational_notification_groups,
    resolved_notification_preferences,
)
from app.permissions import (
    get_default_landing_endpoint,
    get_permission_categories,
    get_permission_definition,
)
from app.utils.filter_state import (
    filters_to_query_args,
    get_filter_defaults,
    normalize_filters,
)
from app.utils.menu_assignments import sync_location_stand_items
from app.utils.numeric import coerce_float
from app.utils.pagination import build_pagination_args, get_per_page
from app.utils.recipe_usage import recipe_item_base_units_per_sale
from app.utils.units import (
    DEFAULT_BASE_UNIT_CONVERSIONS,
    get_allowed_target_units,
    parse_conversion_setting,
    serialize_conversion_setting,
)
from app.utils.pos_import import normalize_pos_alias
from app.utils.text import build_text_match_predicate

auth = Blueprint("auth", __name__)
admin = Blueprint("admin", __name__)

# Only .db files are accepted for database restoration uploads
ALLOWED_BACKUP_EXTENSIONS = {".db"}
SQLITE_INVALID_BACKUP_MARKERS = (
    "file is not a database",
    "database disk image is malformed",
    "malformed database schema",
    "unable to open database file",
    "not a database",
)

IMPORT_FILES = {
    "locations": "example_locations.csv",
    "products": "example_products.csv",
    "gl_codes": "example_gl_codes.csv",
    "items": "example_items.csv",
    "customers": "example_customers.csv",
    "vendors": "example_vendors.csv",
    "users": "example_users.csv",
}


def _redirect_to_default_landing(user=None):
    resolved_user = user or current_user
    return redirect(url_for(get_default_landing_endpoint(resolved_user)))


def _super_admin_count() -> int:
    return User.query.filter_by(is_admin=True).count()


def _permission_input_id(input_prefix: str, value: str) -> str:
    normalized_prefix = re.sub(r"[^a-zA-Z0-9_-]+", "-", input_prefix or "permissions")
    normalized_value = re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "")
    return f"{normalized_prefix}-{normalized_value}".strip("-")


def _selected_permission_categories(
    selected_codes: set[str] | None = None,
    *,
    input_prefix: str = "permissions",
):
    selected_codes = selected_codes or set()
    categories = []
    for category in get_permission_categories():
        permissions = []
        for definition in category["permissions"]:
            permissions.append(
                {
                    "code": definition.code,
                    "label": definition.label,
                    "description": definition.description,
                    "selected": definition.code in selected_codes,
                    "input_id": _permission_input_id(input_prefix, definition.code),
                }
            )
        categories.append(
            {
                "key": category["key"],
                "label": category["label"],
                "permissions": permissions,
                "selected_count": sum(
                    1 for permission in permissions if permission["selected"]
                ),
                "permission_count": len(permissions),
                "toggle_id": _permission_input_id(
                    f"{input_prefix}-category", category["key"]
                ),
            }
        )
    return categories


def _load_permissions_by_codes(codes: list[str] | set[str] | None) -> list[Permission]:
    selected_codes = sorted({code for code in (codes or []) if code})
    if not selected_codes:
        return []
    permissions = (
        Permission.query.filter(Permission.code.in_(selected_codes))
        .order_by(Permission.category, Permission.code)
        .all()
    )
    permissions_by_code = {permission.code: permission for permission in permissions}
    missing_codes = [
        code for code in selected_codes if code not in permissions_by_code
    ]
    if missing_codes:
        for code in missing_codes:
            definition = get_permission_definition(code)
            if definition is None:
                continue
            db.session.add(
                Permission(
                    code=definition.code,
                    category=definition.category,
                    label=definition.label,
                    description=definition.description,
                )
            )
        db.session.flush()
        permissions = (
            Permission.query.filter(Permission.code.in_(selected_codes))
            .order_by(Permission.category, Permission.code)
            .all()
        )
    return permissions


def _load_permission_groups_by_ids(
    group_ids: list[int] | set[int] | tuple[int, ...] | None,
    *,
    exclude_group_id: int | None = None,
) -> list[PermissionGroup]:
    selected_ids = sorted(
        {
            int(group_id)
            for group_id in (group_ids or [])
            if group_id not in (None, "", 0)
        }
    )
    if exclude_group_id is not None:
        selected_ids = [
            group_id for group_id in selected_ids if group_id != int(exclude_group_id)
        ]
    if not selected_ids:
        return []
    return (
        PermissionGroup.query.options(selectinload(PermissionGroup.permissions))
        .filter(PermissionGroup.id.in_(selected_ids))
        .order_by(PermissionGroup.is_system.desc(), PermissionGroup.name)
        .all()
    )


def _resolve_permission_group_codes(
    explicit_codes: list[str] | set[str] | None,
    inherited_group_ids: list[int] | set[int] | tuple[int, ...] | None,
    *,
    exclude_group_id: int | None = None,
) -> tuple[set[str], list[PermissionGroup]]:
    selected_codes = {code for code in (explicit_codes or []) if code}
    inherited_groups = _load_permission_groups_by_ids(
        inherited_group_ids,
        exclude_group_id=exclude_group_id,
    )
    for group in inherited_groups:
        selected_codes.update(
            permission.code for permission in group.permissions if permission.code
        )
    return selected_codes, inherited_groups


def _assign_permission_groups_to_user(user: User, group_ids: list[int] | None) -> None:
    selected_ids = sorted({int(group_id) for group_id in (group_ids or [])})
    groups = (
        PermissionGroup.query.filter(PermissionGroup.id.in_(selected_ids))
        .order_by(PermissionGroup.is_system.desc(), PermissionGroup.name)
        .all()
        if selected_ids
        else []
    )
    user.permission_groups = groups
    user.invalidate_permission_cache()


def _cleanup_restored_user_favorites() -> int:
    """Remove stale favourite endpoints after a backup restore."""

    valid_endpoints = {rule.endpoint for rule in current_app.url_map.iter_rules()}
    users = User.query.all()
    changed = 0

    for user in users:
        favorites = [f for f in (user.favorites or "").split(",") if f]
        filtered = [favorite for favorite in favorites if favorite in valid_endpoints]
        if filtered != favorites:
            user.favorites = ",".join(filtered)
            changed += 1

    if changed:
        db.session.commit()

    return changed


def _apply_restore_favorites_mode(ignore_favorites: bool) -> tuple[str, int]:
    """Apply post-restore favorites behavior and return mode + changed count."""

    if ignore_favorites:
        changed = (
            User.query.filter(User.favorites.isnot(None), User.favorites != "")
            .update({User.favorites: ""}, synchronize_session=False)
        )
        db.session.commit()
        return "ignored", changed

    cleaned_count = _cleanup_restored_user_favorites()
    return "cleaned", cleaned_count


def _refresh_logged_in_user_after_restore() -> None:
    """Reload the authenticated user after a restore rebuilds the database."""

    try:
        if not current_user.is_authenticated:
            return
        user_id = None
        try:
            get_id = getattr(current_user, "get_id", None)
            if callable(get_id):
                user_id = get_id()
        except Exception:
            user_id = None
        if user_id is None:
            user_id = getattr(current_user, "id", None)
        if user_id is None:
            return
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return
        refreshed_user = db.session.get(User, user_id)
        if refreshed_user is None:
            return
        logout_user()
        login_user(refreshed_user, remember=False)
    except Exception:
        current_app.logger.exception("Failed to refresh logged-in user after restore")


def _resolve_restore_mode(raw_mode: str | None) -> str:
    mode = (raw_mode or "").strip().lower()
    if mode in {"permissive", "lenient"}:
        return "permissive"
    return current_app.config.get("RESTORE_MODE_DEFAULT", "strict")


def _is_invalid_backup_sqlalchemy_error(exc: SQLAlchemyError) -> bool:
    details = str(exc).lower()
    return any(marker in details for marker in SQLITE_INVALID_BACKUP_MARKERS)


def _is_schema_evolution_issue(issue: str) -> bool:
    issue_text = (issue or "").lower()
    return (
        "app_schema_version" in issue_text
        or "feature-flag settings" in issue_text
    )


def _split_preflight_issues(issues: list[str]) -> tuple[list[str], list[str]]:
    schema_evolution_issues: list[str] = []
    unresolved_blockers: list[str] = []
    for issue in issues:
        if _is_schema_evolution_issue(issue):
            schema_evolution_issues.append(issue)
        else:
            unresolved_blockers.append(issue)
    return schema_evolution_issues, unresolved_blockers


def _is_strict_restore_blocking_warning(warning: str) -> bool:
    """Return whether a preflight warning should block strict-mode restore."""

    warning_text = (warning or "").lower()
    return any(
        marker in warning_text
        for marker in (
            "foreign key orphan rows found",
            "references missing parent table",
            "column mismatch",
            "not-null violation in",
            "unique violation for",
        )
    )


def _flash_strict_restore_blocked_warning(
    *,
    warning_details: str,
    restore_mode: str,
) -> None:
    """Flash a user-facing message when strict restore is blocked by preflight."""

    flash(f"Compatibility warnings: {warning_details}", "warning")
    flash(
        "Strict restore blocked by preflight data-quality findings.",
        "danger",
    )
    flash(
        "Preflight found rows that would fail strict restore. "
        f"Selected restore mode: {restore_mode}. "
        "Retry in permissive mode to quarantine invalid rows, or repair the backup first.",
        "warning",
    )


def _flash_restore_report(
    *,
    restore_summary,
    unresolved_blockers: list[str],
) -> None:
    report_payload = {
        "mode": restore_summary.mode,
        "inserted_count": restore_summary.inserted_count,
        "skipped_count": restore_summary.skipped_count,
        "table_transform_counts": restore_summary.table_transform_counts or {},
        "field_transform_counts": restore_summary.field_transform_counts or {},
        "repair_report": restore_summary.repair_report or {},
        "unresolved_blockers": unresolved_blockers,
    }
    current_app.logger.info(
        "Restore report payload: %s",
        json.dumps(report_payload, sort_keys=True),
    )
    summary_parts = [
        f"mode={restore_summary.mode}",
        f"inserted={restore_summary.inserted_count}",
        f"skipped={restore_summary.skipped_count}",
    ]
    table_transform_total = sum(
        (restore_summary.table_transform_counts or {}).values()
    )
    if table_transform_total:
        summary_parts.append(f"transforms={table_transform_total}")
    if restore_summary.repaired_count:
        summary_parts.append(f"repaired={restore_summary.repaired_count}")
    if unresolved_blockers:
        summary_parts.append(f"blockers={len(unresolved_blockers)}")
    if restore_summary.quarantine_report:
        summary_parts.append(f"quarantine={restore_summary.quarantine_report}")
    flash(
        "Restore report: " + ", ".join(summary_parts),
        "info",
    )


def _extract_restore_exception_context(exc: SQLAlchemyError) -> dict[str, Any]:
    details = str(exc)
    table_match = re.search(r"(?:table|into)\s+[`\"]?([a-zA-Z_][\w]*)", details, re.IGNORECASE)
    column_match = re.search(r"(?:column|no such column:)\s+[`\"]?([a-zA-Z_][\w]*)", details, re.IGNORECASE)
    context: dict[str, Any] = {}
    if table_match:
        context["table"] = table_match.group(1)
    if column_match:
        context["column"] = column_match.group(1)
    statement = getattr(exc, "statement", None)
    params = getattr(exc, "params", None)
    if statement:
        context["statement"] = statement
    if params:
        context["params"] = str(params)
    original_error = getattr(exc, "orig", None)
    if original_error is not None:
        context["orig"] = str(original_error)
    return context


def _persist_restore_preflight_diagnostic(
    *,
    backups_dir: str,
    filename: str,
    restore_mode: str,
    stage: str,
    exc: SQLAlchemyError,
) -> tuple[str, str]:
    diagnostic_id = uuid.uuid4().hex[:8]
    report_filename = f"restore_preflight_diag_{diagnostic_id}.json"
    report_path = os.path.join(backups_dir, report_filename)
    payload = {
        "diagnostic_id": diagnostic_id,
        "filename": filename,
        "restore_mode": restore_mode,
        "stage": stage,
        "exception_class": type(exc).__name__,
        "exception_message": str(exc),
        "context": _extract_restore_exception_context(exc),
        "created_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    with open(report_path, "w", encoding="utf-8") as report_file:
        json.dump(payload, report_file, indent=2, sort_keys=True)
    logging.getLogger().error(
        "Restore preflight diagnostic captured stage=%s file=%s mode=%s diagnostic_id=%s",
        stage,
        filename,
        restore_mode,
        diagnostic_id,
    )
    log_activity(
        f"Restore preflight diagnostic [{diagnostic_id}] for {filename} "
        f"(mode={restore_mode}, stage={stage}): {json.dumps(payload, sort_keys=True)}"
    )
    return diagnostic_id, report_filename


def _serializer():
    secret_key = current_app.secret_key or current_app.config.get("SECRET_KEY")
    if not secret_key:  # pragma: no cover - configuration guard
        raise RuntimeError("Application secret key is not configured.")
    return URLSafeTimedSerializer(secret_key)


def _reset_token_password_fingerprint(user: User) -> str:
    return hashlib.sha256((user.password or "").encode("utf-8")).hexdigest()


def _normalize_email(value: str | None) -> str:
    return (value or "").strip().lower()


def _find_user_by_email(email: str | None) -> User | None:
    normalized_email = _normalize_email(email)
    if not normalized_email:
        return None
    return User.query.filter(func.lower(User.email) == normalized_email).first()


def _normalize_display_name(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _find_permission_group_by_name(
    name: str | None, *, exclude_group_id: int | None = None
) -> PermissionGroup | None:
    normalized_name = (name or "").strip().casefold()
    if not normalized_name:
        return None
    query = PermissionGroup.query.filter(
        func.lower(PermissionGroup.name) == normalized_name
    )
    if exclude_group_id is not None:
        query = query.filter(PermissionGroup.id != exclude_group_id)
    return query.first()


def _is_pending_invited_user(user: User | None) -> bool:
    return bool(
        user is not None
        and not user.active
        and user.last_login_at is None
    )


def _reset_user_invitation(
    user: User,
    *,
    group_ids: list[int] | None = None,
) -> None:
    user.password = generate_password_hash(os.urandom(16).hex())
    user.active = False
    user.last_active_at = None
    if group_ids is not None:
        _assign_permission_groups_to_user(user, group_ids)


def _send_user_invitation_email(user: User) -> None:
    token = generate_reset_token(user)
    invite_url = url_for("auth.reset_token", token=token, _external=True)
    send_email(
        user.email,
        "You are invited to InvoiceManager",
        f"Click the link to set your password: {invite_url}",
    )


def _deliver_user_invitation(
    user: User,
    *,
    success_message: str,
    activity_message: str,
) -> bool:
    try:
        # Flush so brand-new users receive an ID before token generation.
        db.session.flush()
        _send_user_invitation_email(user)
    except SMTPConfigurationError as exc:
        db.session.rollback()
        current_app.logger.warning(
            "SMTP configuration missing while sending invite to %s: %s",
            user.email,
            exc,
        )
        flash(
            "Email settings are not configured. Please update SMTP settings before sending invites.",
            "danger",
        )
        return False
    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Failed to send invitation email to %s",
            user.email,
        )
        flash(
            "Unable to send invitation email. Please verify SMTP settings and try again.",
            "danger",
        )
        return False

    db.session.commit()
    log_activity(activity_message)
    flash(success_message, "success")
    return True


def _send_password_reset_email_if_possible(user: User) -> None:
    token = generate_reset_token(user)
    reset_url = url_for("auth.reset_token", token=token, _external=True)
    try:
        send_email(
            user.email,
            "Password Reset",
            f"Click the link to reset your password: {reset_url}",
        )
    except SMTPConfigurationError as exc:
        current_app.logger.warning(
            "SMTP configuration missing while sending password reset email to %s: %s",
            user.email,
            exc,
        )
    except Exception:
        current_app.logger.exception(
            "Failed to send password reset email to %s",
            user.email,
        )


def generate_reset_token(user: User) -> str:
    return _serializer().dumps(
        {
            "user_id": user.id,
            "password_fingerprint": _reset_token_password_fingerprint(user),
        }
    )


def verify_reset_token(token: str, max_age: int = 3600):
    try:
        data = _serializer().loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    user = db.session.get(User, data.get("user_id"))
    if user is None:
        return None
    if data.get("password_fingerprint") != _reset_token_password_fingerprint(
        user
    ):
        return None
    return user


PROFILE_NOTIFICATION_BOOLEAN_FIELDS = (
    "notify_schedule_post_email",
    "notify_schedule_post_text",
    "notify_schedule_changes_email",
    "notify_schedule_changes_text",
    "notify_tradeboard_email",
    "notify_tradeboard_text",
)
PROFILE_NOTIFICATION_FIELDS = ("phone_number",) + PROFILE_NOTIFICATION_BOOLEAN_FIELDS


def _build_notification_form(user: User) -> NotificationForm:
    form_kwargs = {"phone_number": user.phone_number or ""}
    for field_name in PROFILE_NOTIFICATION_BOOLEAN_FIELDS:
        form_kwargs[field_name] = bool(getattr(user, field_name, False))
    return NotificationForm(**form_kwargs)


def _build_operational_notification_groups(user: User):
    resolved_preferences = resolved_notification_preferences(user)
    groups = []
    for group in operational_notification_groups():
        event_rows = []
        for definition in group["events"]:
            preferences = resolved_preferences.get(
                definition.key, {"email": False, "text": False}
            )
            email_input_name = notification_preference_input_name(
                definition.key, "email"
            )
            text_input_name = notification_preference_input_name(
                definition.key, "text"
            )
            event_rows.append(
                {
                    "key": definition.key,
                    "label": definition.label,
                    "description": definition.description,
                    "email_enabled": bool(preferences.get("email", False)),
                    "text_enabled": bool(preferences.get("text", False)),
                    "email_input_name": email_input_name,
                    "text_input_name": text_input_name,
                    "email_input_id": notification_preference_input_id(
                        definition.key, "email"
                    ),
                    "text_input_id": notification_preference_input_id(
                        definition.key, "text"
                    ),
                }
            )
        groups.append(
            {
                "key": group["key"],
                "label": group["label"],
                "events": event_rows,
                "selected_count": sum(
                    1
                    for row in event_rows
                    if row["email_enabled"] or row["text_enabled"]
                ),
                "event_count": len(event_rows),
            }
        )
    return groups


def _notifications_submitted(form_data) -> bool:
    return any(field_name in form_data for field_name in PROFILE_NOTIFICATION_FIELDS)


def _apply_notification_preferences(user: User, form: NotificationForm) -> None:
    user.phone_number = form.phone_number.data or None
    for field_name in PROFILE_NOTIFICATION_BOOLEAN_FIELDS:
        setattr(user, field_name, bool(getattr(form, field_name).data))
    granular_preferences = {}
    for group in operational_notification_groups():
        for definition in group["events"]:
            granular_preferences[definition.key] = {
                "email": notification_preference_input_name(
                    definition.key, "email"
                )
                in request.form,
                "text": notification_preference_input_name(definition.key, "text")
                in request.form,
            }
    user.notification_preferences = granular_preferences


@auth.route("/login", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def login():
    """Authenticate a user and start their session."""
    form = LoginForm()
    if form.validate_on_submit():
        email = _normalize_email(form.email.data)
        password = form.password.data

        user = _find_user_by_email(email)

        if not user or not check_password_hash(user.password, password):
            flash("Please check your login details and try again.")
            return redirect(url_for("auth.login"))
        elif not user.active:
            flash("Please contact system admin to activate account.")
            return redirect(url_for("auth.login"))

        now = datetime.utcnow()
        user.last_login_at = now
        user.last_active_at = now
        user.last_forced_login_at = now
        db.session.commit()
        login_user(user, remember=form.remember.data)
        log_activity("Logged in", user.id)
        return _redirect_to_default_landing(user)

    return render_template(
        "auth/login.html", form=form, demo=current_app.config["DEMO"]
    )


@auth.route("/logout", methods=["POST"])
@login_required
def logout():
    """Log the current user out."""
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    user_id = current_user.id
    logout_user()
    log_activity("Logged out", user_id)
    return redirect(url_for("auth.login"))


@admin.route("/zero-threat.html", methods=["GET"])
def zerothreat():
    """Serve the static ZeroThreat domain-verification page."""
    return render_template("auth/zero-threat.html")
    

@auth.route("/reset", methods=["GET", "POST"])
@limiter.limit("3 per hour")
def reset_request():
    """Request a password reset email."""
    form = PasswordResetRequestForm()
    if form.validate_on_submit():
        user = _find_user_by_email(form.email.data)
        if user:
            _send_password_reset_email_if_possible(user)
        flash(
            "If an account exists for that email, a reset link has been sent.",
            "success",
        )
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_request.html", form=form)


@auth.route("/reset/<token>", methods=["GET", "POST"])
def reset_token(token):
    """Set a new password using a reset token."""
    user = verify_reset_token(token)
    if not user:
        flash("Invalid or expired token.", "danger")
        return redirect(url_for("auth.reset_request"))

    form = SetPasswordForm()
    if form.validate_on_submit():
        user.password = generate_password_hash(form.new_password.data)
        if not user.active and user.last_login_at is None:
            user.active = True
        db.session.commit()
        flash("Password updated.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_token.html", form=form)


@auth.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """Allow the current user to change their password."""
    current_user_obj = current_user._get_current_object()
    db.session.expire(current_user_obj, ["active"])
    if not current_user_obj.active:
        logout_user()
        flash(
            "Your account is no longer active. Please contact an administrator.",
            "warning",
        )
        return redirect(url_for("auth.login"))

    form = ChangePasswordForm()
    tz_form = TimezoneForm(timezone=current_user.timezone or "")
    notif_form = _build_notification_form(current_user)
    status_messages = {}
    if request.args.get("password_status") == "updated":
        status_messages["password"] = ("success", "Password updated.")
    if request.args.get("timezone_status") == "updated":
        status_messages["timezone"] = ("success", "Timezone updated.")
    if request.args.get("notifications_status") == "updated":
        status_messages["notifications"] = (
            "success",
            "Notification settings updated.",
        )

    password_submitted = "new_password" in request.form
    timezone_submitted = "timezone" in request.form
    notifications_submitted = _notifications_submitted(request.form)

    if password_submitted and form.validate_on_submit():
        if not check_password_hash(
            current_user.password, form.current_password.data
        ):
            form.current_password.errors.append("Current password incorrect.")
        else:
            current_user.password = generate_password_hash(
                form.new_password.data
            )
            db.session.commit()
            return redirect(url_for("auth.profile", password_status="updated"))
    elif timezone_submitted and tz_form.validate_on_submit():
        current_user.timezone = tz_form.timezone.data or None
        db.session.commit()
        return redirect(url_for("auth.profile", timezone_status="updated"))
    elif notifications_submitted and notif_form.validate_on_submit():
        _apply_notification_preferences(current_user, notif_form)
        db.session.commit()
        log_activity(f"Updated notification preferences for user {current_user.id}")
        return redirect(
            url_for("auth.profile", notifications_status="updated")
        )

    transfers = (
        Transfer.query.filter_by(user_id=current_user.id)
        .order_by(Transfer.date_created.desc(), Transfer.id.desc())
        .all()
    )
    invoices = (
        Invoice.query.filter_by(user_id=current_user.id)
        .order_by(Invoice.date_created.desc(), Invoice.id.desc())
        .all()
    )
    return render_template(
        "profile.html",
        user=current_user,
        form=form,
        tz_form=tz_form,
        notif_form=notif_form,
        operational_notification_groups=_build_operational_notification_groups(
            current_user
        ),
        status_messages=status_messages,
        password_submitted=password_submitted,
        timezone_submitted=timezone_submitted,
        notifications_submitted=notifications_submitted,
        transfers=transfers,
        invoices=invoices,
    )


@auth.route("/favorite/<path:link>", methods=["POST"])
@login_required
def toggle_favorite(link):
    """Toggle a navigation link as favourite for the current user."""
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        abort(400)
    current_user.toggle_favorite(link)
    db.session.commit()
    referrer = request.form.get("next") or request.referrer
    if referrer:
        safe_referrer = referrer.replace("\\", "")
        parsed = urlparse(safe_referrer)
        if not parsed.scheme and not parsed.netloc:
            return redirect(safe_referrer)
    return _redirect_to_default_landing()


@admin.route("/user_profile/<int:user_id>", methods=["GET", "POST"])
@login_required
def user_profile(user_id):
    """View or update another user's profile."""
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    form = SetPasswordForm()
    tz_form = TimezoneForm(timezone=user.timezone or "")
    notif_form = _build_notification_form(user)
    status_messages = {}
    if request.args.get("password_status") == "updated":
        status_messages["password"] = ("success", "Password updated.")
    if request.args.get("timezone_status") == "updated":
        status_messages["timezone"] = ("success", "Timezone updated.")
    if request.args.get("notifications_status") == "updated":
        status_messages["notifications"] = (
            "success",
            "Notification settings updated.",
        )

    password_submitted = "new_password" in request.form
    timezone_submitted = "timezone" in request.form
    notifications_submitted = _notifications_submitted(request.form)

    if password_submitted and form.validate_on_submit():
        user.password = generate_password_hash(form.new_password.data)
        db.session.commit()
        return redirect(
            url_for(
                "admin.user_profile", user_id=user_id, password_status="updated"
            )
        )
    elif timezone_submitted and tz_form.validate_on_submit():
        user.timezone = tz_form.timezone.data or None
        db.session.commit()
        return redirect(
            url_for(
                "admin.user_profile", user_id=user_id, timezone_status="updated"
            )
        )
    elif notifications_submitted and notif_form.validate_on_submit():
        _apply_notification_preferences(user, notif_form)
        db.session.commit()
        log_activity(f"Updated notification preferences for user {user.id}")
        return redirect(
            url_for(
                "admin.user_profile",
                user_id=user_id,
                notifications_status="updated",
            )
        )

    transfers = (
        Transfer.query.filter_by(user_id=user.id)
        .order_by(Transfer.date_created.desc(), Transfer.id.desc())
        .all()
    )
    invoices = (
        Invoice.query.filter_by(user_id=user.id)
        .order_by(Invoice.date_created.desc(), Invoice.id.desc())
        .all()
    )
    return render_template(
        "profile.html",
        user=user,
        form=form,
        tz_form=tz_form,
        notif_form=notif_form,
        operational_notification_groups=_build_operational_notification_groups(user),
        status_messages=status_messages,
        password_submitted=password_submitted,
        timezone_submitted=timezone_submitted,
        notifications_submitted=notifications_submitted,
        transfers=transfers,
        invoices=invoices,
    )


@admin.route("/activate_user/<int:user_id>", methods=["POST"])
@login_required
def activate_user(user_id):
    """Activate a user account."""
    form = CSRFOnlyForm()
    if not form.validate_on_submit():
        abort(400)

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    user.active = True
    db.session.commit()
    log_activity(f"Activated user {user_id}")
    notify_users_for_event(
        event_key="user_activated",
        subject=f"User activated: {user.email}",
        body=f"{current_user.email} activated user {user.email}.",
        sms_body=f"User activated: {user.email}",
        exclude_user_ids={current_user.id},
    )
    flash("User account activated.", "success")
    return redirect(
        url_for("admin.users")
    )  # Redirect to the user control panel


@admin.route("/controlpanel/users", methods=["GET", "POST"])
@login_required
def users():
    """Admin interface for managing users."""
    users = User.query.options(selectinload(User.permission_groups)).all()
    users = sorted(users, key=lambda user: (user.sort_key, user.email.casefold()))

    form = UserForm()
    invite_form = InviteUserForm()

    invite_submitted = request.method == "POST" and (
        invite_form.submit.name in request.form or "email" in request.form
    )
    if invite_submitted:
        if invite_form.validate_on_submit():
            email = _normalize_email(invite_form.email.data)
            display_name = _normalize_display_name(invite_form.display_name.data)
            existing = _find_user_by_email(email)
            if existing:
                if _is_pending_invited_user(existing):
                    existing.display_name = display_name or None
                    _reset_user_invitation(
                        existing, group_ids=invite_form.group_ids.data
                    )
                    _deliver_user_invitation(
                        existing,
                        success_message="Invitation re-sent.",
                        activity_message=f"Re-sent invite to user {email}",
                    )
                else:
                    flash(
                        "User already exists. Use password reset if they need a new setup email.",
                        "danger",
                    )
            else:
                new_user = User(
                    email=email,
                    display_name=display_name or None,
                    password="",
                    active=False,
                    is_admin=False,
                )
                _reset_user_invitation(new_user, group_ids=invite_form.group_ids.data)
                db.session.add(new_user)
                invitation_sent = _deliver_user_invitation(
                    new_user,
                    success_message="Invitation sent.",
                    activity_message=f"Invited user {email}",
                )
                if invitation_sent:
                    notify_users_for_event(
                        event_key="user_invited",
                        subject=f"User invited: {email}",
                        body=f"{current_user.email} invited {email} to InvoiceManager.",
                        sms_body=f"User invited: {email}",
                        exclude_user_ids={current_user.id},
                    )
            return redirect(url_for("admin.users"))
        return render_template(
            "admin/view_users.html",
            users=users,
            form=form,
            invite_form=invite_form,
        )

    if request.method == "POST" and request.form.get("action"):
        if not form.validate_on_submit():
            abort(400)
        user_id = request.form.get("user_id", type=int)
        if user_id is None:
            user_id = request.args.get("user_id", type=int)
        action = request.form.get("action")

        user = db.session.get(User, user_id)
        if user:
            notification_payload = None
            notification_event_key = None
            if action == "toggle_active":
                if _is_pending_invited_user(user):
                    flash(
                        "Pending invites cannot be activated manually. Re-send or delete the invite instead.",
                        "warning",
                    )
                    return redirect(url_for("admin.users"))
                user.active = not user.active
                if not user.active:
                    user.last_active_at = None
                log_activity(f"Toggled active for user {user_id}")
                notification_payload = {
                    "subject": (
                        f"User {'activated' if user.active else 'deactivated'}: {user.email}"
                    ),
                    "body": (
                        f"{current_user.email} "
                        f"{'activated' if user.active else 'deactivated'} "
                        f"user {user.email}."
                    ),
                    "sms_body": (
                        f"User {'activated' if user.active else 'deactivated'}: {user.email}"
                    ),
                }
                notification_event_key = (
                    "user_activated" if user.active else "user_deactivated"
                )
            elif action == "resend_invite":
                if not _is_pending_invited_user(user):
                    flash("Only pending invites can be re-sent.", "warning")
                    return redirect(url_for("admin.users"))
                _reset_user_invitation(user)
                _deliver_user_invitation(
                    user,
                    success_message="Invitation re-sent.",
                    activity_message=f"Re-sent invite to user {user.email}",
                )
                return redirect(url_for("admin.users"))
            elif action == "toggle_super_admin":
                if not current_user.is_super_admin:
                    abort(403)
                if user.is_super_admin and _super_admin_count() <= 1:
                    flash("At least one super admin is required.", "danger")
                    return redirect(url_for("admin.users"))
                user.is_admin = not user.is_admin
                user.invalidate_permission_cache()
                log_activity(
                    f"Toggled super admin for user {user_id} to {user.is_admin}"
                )
                notification_payload = {
                    "subject": f"User access updated: {user.email}",
                    "body": (
                        f"{current_user.email} updated super-admin access for "
                        f"user {user.email}."
                    ),
                    "sms_body": f"User access updated: {user.email}",
                }
                notification_event_key = "user_access_updated"
            else:
                flash("Unsupported action.", "danger")
                return redirect(url_for("admin.users"))
            db.session.commit()
            if notification_payload and notification_event_key:
                notify_users_for_event(
                    event_key=notification_event_key,
                    exclude_user_ids={current_user.id},
                    **notification_payload,
                )
            flash("User updated successfully", "success")
        else:
            flash("User not found", "danger")

        return redirect(url_for("admin.users"))

    return render_template(
        "admin/view_users.html",
        users=users,
        form=form,
        invite_form=invite_form,
    )


@admin.route("/controlpanel/users/<int:user_id>/access", methods=["GET", "POST"])
@login_required
def user_access(user_id):
    """View and update a user's assigned permission groups."""
    user = (
        User.query.options(selectinload(User.permission_groups))
        .filter_by(id=user_id)
        .first()
    )
    if user is None:
        abort(404)

    access_form = UserAccessForm(prefix="access")
    if request.method == "GET":
        access_form.display_name.data = user.display_name or ""
        access_form.group_ids.data = [
            group.id for group in user.permission_groups
        ]
    elif request.method == "POST":
        if access_form.validate_on_submit():
            user.display_name = (
                _normalize_display_name(access_form.display_name.data) or None
            )
            _assign_permission_groups_to_user(user, access_form.group_ids.data)
            db.session.commit()
            log_activity(f"Updated permission groups for user {user.email}")
            flash("User access updated.", "success")
            return redirect(url_for("admin.user_access", user_id=user.id))
    else:
        access_form.display_name.data = access_form.display_name.data or (
            user.display_name or ""
        )
        access_form.group_ids.data = access_form.group_ids.data or [
            group.id for group in user.permission_groups
        ]

    return render_template(
        "admin/user_access.html",
        managed_user=user,
        access_form=access_form,
    )


@admin.route("/delete_user/<int:user_id>", methods=["POST"])
@login_required
def delete_user(user_id):
    """Archive a user or delete an unused pending invite."""
    user_to_delete = db.session.get(User, user_id)
    if user_to_delete is None:
        abort(404)
    if user_to_delete.is_super_admin and _super_admin_count() <= 1:
        flash("At least one super admin is required.", "danger")
        return redirect(url_for("admin.users"))
    if _is_pending_invited_user(user_to_delete):
        invite_email = user_to_delete.email
        db.session.delete(user_to_delete)
        db.session.commit()
        log_activity(f"Deleted pending invite {invite_email}")
        notify_users_for_event(
            event_key="user_pending_invite_deleted",
            subject=f"Pending invite deleted: {invite_email}",
            body=f"{current_user.email} deleted the pending invite for {invite_email}.",
            sms_body=f"Pending invite deleted: {invite_email}",
            exclude_user_ids={current_user.id},
        )
        flash("Pending invite deleted.", "success")
        return redirect(url_for("admin.users"))
    user_to_delete.active = False
    db.session.commit()
    log_activity(f"Archived user {user_id}")
    notify_users_for_event(
        event_key="user_archived",
        subject=f"User archived: {user_to_delete.email}",
        body=f"{current_user.email} archived user {user_to_delete.email}.",
        sms_body=f"User archived: {user_to_delete.email}",
        exclude_user_ids={current_user.id},
    )
    flash("User archived successfully.", "success")
    return redirect(url_for("admin.users"))


@admin.route("/controlpanel/permission-groups", methods=["GET"])
@login_required
def permission_groups():
    """List permission groups."""
    groups = (
        PermissionGroup.query.options(
            selectinload(PermissionGroup.permissions),
            selectinload(PermissionGroup.users),
        )
        .order_by(PermissionGroup.is_system.desc(), PermissionGroup.name)
        .all()
    )
    can_manage_groups = current_user.has_permission("permission_groups.manage")
    can_manage_permissions = current_user.has_permission("permissions.manage")
    delete_form = DeleteForm()

    return render_template(
        "admin/permission_groups.html",
        groups=groups,
        can_manage_groups=can_manage_groups,
        can_manage_permissions=can_manage_permissions,
        delete_form=delete_form,
    )


@admin.route("/controlpanel/permission-groups/create", methods=["GET", "POST"])
@login_required
def create_permission_group():
    """Create a permission group."""
    if not current_user.has_permission("permission_groups.manage"):
        abort(403)

    create_form = PermissionGroupForm(prefix="create")
    can_manage_permissions = current_user.has_permission("permissions.manage")

    if request.method == "POST" and create_form.submit.name in request.form:
        posted_permission_codes = {
            code for code in request.form.getlist(create_form.permissions.name) if code
        }
        inherited_group_ids = request.form.getlist(create_form.inherited_group_ids.name)
        if posted_permission_codes and not can_manage_permissions:
            abort(403)
        if inherited_group_ids and not can_manage_permissions:
            abort(403)

        is_valid = create_form.validate_on_submit()
        name = (create_form.name.data or "").strip()

        if not name:
            create_form.name.errors.append("Group name is required.")
            is_valid = False

        if is_valid:
            existing = _find_permission_group_by_name(name)
            if existing:
                create_form.name.errors.append(
                    "A permission group with that name already exists."
                )
                is_valid = False

        if is_valid:
            group = PermissionGroup(
                name=name,
                description=(create_form.description.data or "").strip() or None,
            )
            if can_manage_permissions:
                effective_codes, _ = _resolve_permission_group_codes(
                    posted_permission_codes,
                    create_form.inherited_group_ids.data,
                )
                group.permissions = _load_permissions_by_codes(effective_codes)
            db.session.add(group)
            db.session.commit()
            log_activity(f"Created permission group {group.name}")
            flash("Permission group created.", "success")
            return redirect(url_for("admin.edit_permission_group", group_id=group.id))

    selected_codes = {
        code for code in (create_form.permissions.data or []) if code
    }
    if request.method == "POST" and can_manage_permissions:
        selected_codes, _ = _resolve_permission_group_codes(
            request.form.getlist(create_form.permissions.name),
            request.form.getlist(create_form.inherited_group_ids.name),
        )
    create_permission_categories = _selected_permission_categories(
        selected_codes,
        input_prefix=create_form.permissions.id,
    )

    return render_template(
        "admin/create_permission_group.html",
        create_form=create_form,
        create_permission_categories=create_permission_categories,
        can_manage_permissions=can_manage_permissions,
    )


@admin.route(
    "/controlpanel/permission-groups/<int:group_id>",
    methods=["GET", "POST"],
)
@login_required
def edit_permission_group(group_id):
    """View or edit a permission group."""
    group = (
        PermissionGroup.query.options(
            selectinload(PermissionGroup.permissions),
            selectinload(PermissionGroup.users),
        )
        .filter_by(id=group_id)
        .first()
    )
    if group is None:
        abort(404)

    group_form = PermissionGroupForm(prefix="group", obj=group, exclude_group_id=group.id)
    can_manage_group = current_user.has_permission("permission_groups.manage")
    can_manage_permissions = current_user.has_permission("permissions.manage")
    can_update_group = can_manage_group or can_manage_permissions
    existing_codes = [permission.code for permission in group.permissions]

    if request.method == "GET":
        group_form.permissions.data = existing_codes
        if not can_manage_group:
            group_form.name.data = group.name
            group_form.description.data = group.description or ""
    elif request.method == "POST" and group_form.submit.name in request.form:
        if not can_update_group:
            abort(403)
        if group.is_system:
            flash("System permission groups cannot be edited.", "warning")
            return redirect(url_for("admin.edit_permission_group", group_id=group.id))

        posted_permission_codes = {
            code for code in request.form.getlist(group_form.permissions.name) if code
        }
        inherited_group_ids = request.form.getlist(group_form.inherited_group_ids.name)
        if posted_permission_codes and not can_manage_permissions:
            abort(403)
        if inherited_group_ids and not can_manage_permissions:
            abort(403)
        if not can_manage_group:
            if (group_form.name.data or "").strip() != group.name:
                abort(403)
            if (group_form.description.data or "").strip() != (
                group.description or ""
            ):
                abort(403)
            group_form.name.data = group.name
            group_form.description.data = group.description or ""
        if not can_manage_permissions:
            group_form.permissions.data = existing_codes

        is_valid = group_form.validate_on_submit()
        name = (group_form.name.data or "").strip()

        if can_manage_group and not name:
            group_form.name.errors.append("Group name is required.")
            is_valid = False

        if can_manage_group and is_valid:
            duplicate = _find_permission_group_by_name(
                name, exclude_group_id=group.id
            )
            if duplicate:
                group_form.name.errors.append(
                    "A permission group with that name already exists."
                )
                is_valid = False

        if is_valid:
            if can_manage_group:
                group.name = name
                group.description = (group_form.description.data or "").strip() or None
            if can_manage_permissions:
                effective_codes, _ = _resolve_permission_group_codes(
                    posted_permission_codes,
                    group_form.inherited_group_ids.data,
                    exclude_group_id=group.id,
                )
                group.permissions = _load_permissions_by_codes(effective_codes)
            db.session.commit()
            log_activity(f"Updated permission group {group.name}")
            flash("Permission group updated.", "success")
            return redirect(url_for("admin.edit_permission_group", group_id=group.id))

    selected_codes = {code for code in (group_form.permissions.data or []) if code}
    if request.method == "POST" and can_manage_permissions:
        selected_codes, _ = _resolve_permission_group_codes(
            request.form.getlist(group_form.permissions.name),
            request.form.getlist(group_form.inherited_group_ids.name),
            exclude_group_id=group.id,
        )
    permission_categories = _selected_permission_categories(
        selected_codes,
        input_prefix=group_form.permissions.id,
    )

    return render_template(
        "admin/edit_permission_group.html",
        group=group,
        group_form=group_form,
        permission_categories=permission_categories,
        can_manage_group=can_manage_group,
        can_manage_permissions=can_manage_permissions,
        can_update_group=can_update_group,
    )


@admin.route(
    "/controlpanel/permission-groups/<int:group_id>/delete",
    methods=["POST"],
)
@login_required
def delete_permission_group(group_id):
    """Delete a permission group."""
    form = DeleteForm()
    if not form.validate_on_submit():
        abort(400)

    group = db.session.get(PermissionGroup, group_id)
    if group is None:
        abort(404)
    if group.is_system:
        flash("System permission groups cannot be deleted.", "warning")
        return redirect(url_for("admin.permission_groups"))

    group_name = group.name
    db.session.delete(group)
    db.session.commit()
    log_activity(f"Deleted permission group {group_name}")
    flash("Permission group deleted.", "success")
    return redirect(url_for("admin.permission_groups"))


@admin.route("/controlpanel/permissions", methods=["GET"])
@login_required
def permission_catalog():
    """Display the full permission catalog and group assignments."""
    permissions = (
        Permission.query.options(selectinload(Permission.groups))
        .order_by(Permission.category, Permission.code)
        .all()
    )
    permissions_by_code = {permission.code: permission for permission in permissions}
    permission_categories = []

    for category in get_permission_categories():
        category_rows = []
        for definition in category["permissions"]:
            permission = permissions_by_code.get(definition.code)
            category_rows.append(
                {
                    "code": definition.code,
                    "label": definition.label,
                    "description": definition.description,
                    "groups": sorted(
                        permission.groups,
                        key=lambda group: (not group.is_system, group.name.casefold()),
                    )
                    if permission is not None
                    else [],
                }
            )
        permission_categories.append(
            {
                "key": category["key"],
                "label": category["label"],
                "permissions": category_rows,
            }
        )

    return render_template(
        "admin/permission_catalog.html",
        permission_categories=permission_categories,
    )


@admin.route("/controlpanel/backups", methods=["GET"])
@login_required
def backups():
    """List available database backups."""
    return _render_backups_page()


def _render_backups_page():
    """Render the backup management page with the current backup listing."""

    from flask import current_app

    backups_dir = current_app.config["BACKUP_FOLDER"]
    os.makedirs(backups_dir, exist_ok=True)
    files = sorted(
        filename
        for filename in os.listdir(backups_dir)
        if filename.lower().endswith(".db")
        and os.path.isfile(os.path.join(backups_dir, filename))
    )
    create_form = CreateBackupForm()
    restore_form = RestoreBackupForm()
    restore_form.restore_mode.data = _resolve_restore_mode(
        current_app.config.get("RESTORE_MODE_DEFAULT")
    )
    return render_template(
        "admin/backups.html",
        backups=files,
        create_form=create_form,
        restore_form=restore_form,
        can_create_backups=current_user.has_permission("backups.create"),
        can_restore_backups=current_user.has_permission("backups.restore"),
        can_download_backups=current_user.has_permission("backups.download"),
    )


@admin.route("/controlpanel/backups/create", methods=["POST"])
@login_required
def create_backup_route():
    """Create a new database backup."""
    form = CreateBackupForm()
    if form.validate_on_submit():
        filename = create_backup()
        log_activity(f"Created backup {filename}")
        flash("Backup created: " + filename, "success")
    return redirect(url_for("admin.backups"))


@admin.route("/controlpanel/backups/restore", methods=["POST"])
@login_required
def restore_backup_route():
    """Restore the database from an uploaded backup."""
    form = RestoreBackupForm()
    actor_user_id = getattr(current_user, "id", None)
    warning_details = ""
    uploaded_path = None

    def _cleanup_uploaded_file():
        nonlocal uploaded_path
        if uploaded_path and os.path.exists(uploaded_path):
            os.remove(uploaded_path)
        uploaded_path = None

    if form.validate_on_submit():
        file = form.file.data
        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        file.seek(0, os.SEEK_END)
        size = file.tell()
        file.seek(0)
        if ext not in ALLOWED_BACKUP_EXTENSIONS:
            flash("Only .db files are allowed.", "error")
            return redirect(url_for("admin.backups"))
        if size > MAX_BACKUP_SIZE:
            flash("File is too large.", "error")
            return redirect(url_for("admin.backups"))
        from flask import current_app

        backups_dir = current_app.config["BACKUP_FOLDER"]
        os.makedirs(backups_dir, exist_ok=True)
        uploaded_name = f"restore-upload-{uuid.uuid4().hex}-{filename}"
        filepath = os.path.join(backups_dir, uploaded_name)
        uploaded_path = filepath
        file.save(filepath)
        unresolved_blockers: list[str] = []
        restore_mode = _resolve_restore_mode(form.restore_mode.data)
        try:
            compatibility = validate_backup_file_compatibility(filepath)
        except SQLAlchemyError as exc:
            if _is_invalid_backup_sqlalchemy_error(exc):
                _cleanup_uploaded_file()
                flash("Invalid backup database file.", "error")
                return redirect(url_for("admin.backups"))
            compatibility = None
            diagnostic_id, report_filename = _persist_restore_preflight_diagnostic(
                backups_dir=backups_dir,
                filename=filename,
                restore_mode=restore_mode,
                stage="preflight",
                exc=exc,
            )
            current_app.logger.exception(
                "Restore preflight adapter fallback for file=%s mode=%s stage=%s diagnostic_id=%s",
                filename,
                restore_mode,
                "preflight",
                diagnostic_id,
            )
            flash(
                "Preflight schema inspection could not fully validate this backup. "
                "Proceeding with legacy adapter/transform restore workflow.",
                "warning",
            )
            flash(
                f"Restore diagnostic ID: {diagnostic_id} (report: {report_filename})",
                "warning",
            )
        if compatibility is not None:
            schema_evolution_issues, unresolved_blockers = _split_preflight_issues(
                compatibility.issues
            )
            if schema_evolution_issues:
                compatibility.warnings.extend(schema_evolution_issues)
            compatibility.issues = unresolved_blockers

        if compatibility is not None and not compatibility.compatible:
            details = "; ".join(unresolved_blockers)
            current_app.logger.warning(
                "Restore preflight incompatibility detected for %s: %s",
                filename,
                details,
            )
            log_activity(
                f"Restore blocked due to compatibility errors for {filename}: {details}",
                actor_user_id,
            )
            flash(
                "Warning: Incompatible backup: this backup is missing critical database "
                "structures and cannot be restored safely.",
                "danger",
            )
            flash(f"Compatibility errors: {details}", "danger")
            _cleanup_uploaded_file()
            return redirect(url_for("admin.backups"))

        if compatibility is not None and compatibility.warnings:
            warning_details = "; ".join(compatibility.warnings)
            current_app.logger.warning(
                "Restore preflight compatibility warnings for %s: %s",
                filename,
                warning_details,
            )
            log_activity(
                f"Restore compatibility warnings detected for {filename}: {warning_details}",
                actor_user_id,
            )
            strict_restore_blockers = [
                warning
                for warning in compatibility.warnings
                if _is_strict_restore_blocking_warning(warning)
            ]
            if restore_mode == "strict" and strict_restore_blockers:
                _flash_strict_restore_blocked_warning(
                    warning_details=warning_details,
                    restore_mode=restore_mode,
                )
                log_activity(
                    f"Strict restore blocked for {filename}: {warning_details}",
                    actor_user_id,
                )
                _cleanup_uploaded_file()
                return redirect(url_for("admin.backups"))
        try:
            restore_summary = restore_backup(filepath, restore_mode=restore_mode)
        except RestoreBackupError as exc:
            failure_class = type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__
            restore_details = str(exc)
            current_app.logger.exception(
                "Restore runtime failure for %s (%s): %s",
                filename,
                failure_class,
                restore_details,
            )
            log_activity(
                f"Restore failed for {filename} [{failure_class}]: {restore_details}",
                actor_user_id,
            )
            flash(
                f"Restore failed ({failure_class}): {restore_details}",
                "danger",
            )
            _cleanup_uploaded_file()
            return redirect(url_for("admin.backups"))
        _cleanup_uploaded_file()
        mode, changed_count = _apply_restore_favorites_mode(
            bool(form.ignore_favorites.data)
        )
        _refresh_logged_in_user_after_restore()
        if warning_details:
            flash(f"Compatibility warnings: {warning_details}", "warning")
            flash("Restored with compatibility warnings.", "warning")
            flash(
                "Preflight detected data-quality risks. "
                f"Selected restore mode: {restore_mode}. "
                "Use permissive mode to quarantine invalid rows, or strict mode to fail on first violation.",
                "warning",
            )

        if mode == "ignored":
            log_activity(
                f"Cleared favorites for {changed_count} user(s) after restore {filename} (ignore_favorites=true)",
                actor_user_id,
            )
            flash(
                f"Backup restored from {filename}. Favorites mode: ignored backup favorites and cleared all user favorites.",
                "success",
            )
        else:
            if changed_count:
                log_activity(
                    f"Removed stale favorites for {changed_count} user(s) after restore {filename}",
                    actor_user_id,
                )
            flash(
                f"Backup restored from {filename}. Favorites mode: pruned invalid favorites.",
                "success",
            )
        if restore_summary.skipped_count:
            flash(
                "Partial restore completed in permissive mode: "
                f"inserted {restore_summary.inserted_count} rows, skipped "
                f"{restore_summary.skipped_count} invalid row(s). "
                f"Quarantine report: {restore_summary.quarantine_report}.",
                "warning",
            )
            log_activity(
                f"Restore partial outcome for {filename}: inserted={restore_summary.inserted_count}, "
                f"skipped={restore_summary.skipped_count}, quarantine={restore_summary.quarantine_report}, "
                f"tables={','.join(restore_summary.affected_tables)}",
                actor_user_id,
            )
        _flash_restore_report(
            restore_summary=restore_summary,
            unresolved_blockers=unresolved_blockers,
        )
        restore_message = (
            f"Restored backup {filename} with compatibility warnings "
            f"(favorites_mode={mode})"
            if compatibility is not None and compatibility.warnings
            else f"Restored backup {filename} (favorites_mode={mode})"
        )
        restore_message += (
            f" [restore_mode={restore_summary.mode}, inserted={restore_summary.inserted_count}, "
            f"skipped={restore_summary.skipped_count}, "
            f"table_transforms={sum((restore_summary.table_transform_counts or {}).values())}]"
        )
        log_activity(restore_message, actor_user_id)
    else:
        for error in form.file.errors:
            flash(error, "error")
    return redirect(url_for("admin.backups"))


@admin.route("/controlpanel/backups/restore/<path:filename>", methods=["POST"])
@login_required
def restore_backup_file(filename):
    """Restore the database from an existing backup file."""
    from flask import current_app

    actor_user_id = getattr(current_user, "id", None)
    warning_details = ""
    backups_dir = current_app.config["BACKUP_FOLDER"]
    try:
        filepath = safe_join(backups_dir, filename)
    except NotFound:
        abort(404)
    if filepath is None or not os.path.isfile(filepath):
        abort(404)
    fname = os.path.basename(filepath)
    unresolved_blockers: list[str] = []
    raw_mode = flask.request.values.get("restore_mode")
    restore_permissive_values = {
        value.lower()
        for value in flask.request.values.getlist("restore_permissive")
        if value
    }
    is_permissive = bool(
        restore_permissive_values & {"1", "true", "on", "yes"}
    )
    selected_mode = "permissive" if is_permissive else (raw_mode or "strict")
    restore_mode = _resolve_restore_mode(selected_mode)
    try:
        compatibility = validate_backup_file_compatibility(filepath)
    except SQLAlchemyError as exc:
        if _is_invalid_backup_sqlalchemy_error(exc):
            flash("Invalid backup database file.", "error")
            return redirect(url_for("admin.backups"))
        compatibility = None
        diagnostic_id, report_filename = _persist_restore_preflight_diagnostic(
            backups_dir=backups_dir,
            filename=fname,
            restore_mode=restore_mode,
            stage="preflight",
            exc=exc,
        )
        current_app.logger.exception(
            "Restore preflight adapter fallback for file=%s mode=%s stage=%s diagnostic_id=%s",
            fname,
            restore_mode,
            "preflight",
            diagnostic_id,
        )
        flash(
            "Preflight schema inspection could not fully validate this backup. "
            "Proceeding with legacy adapter/transform restore workflow.",
            "warning",
        )
        flash(
            f"Restore diagnostic ID: {diagnostic_id} (report: {report_filename})",
            "warning",
        )

    if compatibility is not None:
        schema_evolution_issues, unresolved_blockers = _split_preflight_issues(
            compatibility.issues
        )
        if schema_evolution_issues:
            compatibility.warnings.extend(schema_evolution_issues)
        compatibility.issues = unresolved_blockers

    if compatibility is not None and not compatibility.compatible:
        details = "; ".join(unresolved_blockers)
        current_app.logger.warning(
            "Restore preflight incompatibility detected for %s: %s",
            fname,
            details,
        )
        log_activity(
            f"Restore blocked due to compatibility errors for {fname}: {details}",
            actor_user_id,
        )
        flash(
            "Warning: Incompatible backup: this backup is missing critical database "
            "structures and cannot be restored safely.",
            "danger",
        )
        flash(f"Compatibility errors: {details}", "danger")
        return redirect(url_for("admin.backups"))

    if compatibility is not None and compatibility.warnings:
        warning_details = "; ".join(compatibility.warnings)
        current_app.logger.warning(
            "Restore preflight compatibility warnings for %s: %s",
            fname,
            warning_details,
        )
        log_activity(
            f"Restore compatibility warnings detected for {fname}: {warning_details}",
            actor_user_id,
        )
        strict_restore_blockers = [
            warning
            for warning in compatibility.warnings
            if _is_strict_restore_blocking_warning(warning)
        ]
        if restore_mode == "strict" and strict_restore_blockers:
            _flash_strict_restore_blocked_warning(
                warning_details=warning_details,
                restore_mode=restore_mode,
            )
            log_activity(
                f"Strict restore blocked for {fname}: {warning_details}",
                actor_user_id,
            )
            return redirect(url_for("admin.backups"))
    try:
        restore_summary = restore_backup(filepath, restore_mode=restore_mode)
    except RestoreBackupError as exc:
        failure_class = type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__
        restore_details = str(exc)
        current_app.logger.exception(
            "Restore runtime failure for %s (%s): %s",
            fname,
            failure_class,
            restore_details,
        )
        log_activity(
            f"Restore failed for {fname} [{failure_class}]: {restore_details}",
            actor_user_id,
        )
        flash(
            f"Restore failed ({failure_class}): {restore_details}",
            "danger",
        )
        return redirect(url_for("admin.backups"))
    ignore_values = {
        value.lower()
        for value in flask.request.values.getlist("ignore_favorites")
        if value
    }
    ignore_favorites = bool(ignore_values & {"1", "true", "on", "yes"})
    mode, changed_count = _apply_restore_favorites_mode(ignore_favorites)
    _refresh_logged_in_user_after_restore()
    if warning_details:
        flash(f"Compatibility warnings: {warning_details}", "warning")
        flash("Restored with compatibility warnings.", "warning")
        flash(
            "Preflight detected data-quality risks. "
            f"Selected restore mode: {restore_mode}. "
            "Use permissive mode to quarantine invalid rows, or strict mode to fail on first violation.",
            "warning",
        )

    if mode == "ignored":
        log_activity(
            f"Cleared favorites for {changed_count} user(s) after restore {fname} (ignore_favorites=true)",
            actor_user_id,
        )
        flash(
            f"Backup restored from {fname}. Favorites mode: ignored backup favorites and cleared all user favorites.",
            "success",
        )
    else:
        if changed_count:
            log_activity(
                f"Removed stale favorites for {changed_count} user(s) after restore {fname}",
                actor_user_id,
            )
        flash(
            f"Backup restored from {fname}. Favorites mode: pruned invalid favorites.",
            "success",
        )
    if restore_summary.skipped_count:
        flash(
            "Partial restore completed in permissive mode: "
            f"inserted {restore_summary.inserted_count} rows, skipped "
            f"{restore_summary.skipped_count} invalid row(s). "
            f"Quarantine report: {restore_summary.quarantine_report}.",
            "warning",
        )
        log_activity(
            f"Restore partial outcome for {fname}: inserted={restore_summary.inserted_count}, "
            f"skipped={restore_summary.skipped_count}, quarantine={restore_summary.quarantine_report}, "
            f"tables={','.join(restore_summary.affected_tables)}",
            actor_user_id,
        )
    _flash_restore_report(
        restore_summary=restore_summary,
        unresolved_blockers=unresolved_blockers,
    )
    restore_message = (
        f"Restored backup {fname} with compatibility warnings "
        f"(favorites_mode={mode})"
        if compatibility is not None and compatibility.warnings
        else f"Restored backup {fname} (favorites_mode={mode})"
    )
    restore_message += (
        f" [restore_mode={restore_summary.mode}, inserted={restore_summary.inserted_count}, "
        f"skipped={restore_summary.skipped_count}, "
        f"table_transforms={sum((restore_summary.table_transform_counts or {}).values())}]"
    )
    log_activity(restore_message, actor_user_id)
    return redirect(url_for("admin.backups"))


@admin.route("/controlpanel/backups/download/<path:filename>", methods=["GET"])
@login_required
def download_backup(filename):
    """Download a backup file."""
    from flask import current_app, send_from_directory

    backups_dir = current_app.config["BACKUP_FOLDER"]
    try:
        filepath = safe_join(backups_dir, filename)
    except NotFound:
        abort(404)
    if filepath is None or not os.path.isfile(filepath):
        abort(404)
    safe_filename = os.path.basename(filepath)
    log_activity(f"Downloaded backup {safe_filename}")
    return send_from_directory(backups_dir, safe_filename, as_attachment=True)


@admin.route("/controlpanel/activity", methods=["GET"])
@login_required
def activity_logs():
    """Display a log of user actions."""
    scope = request.endpoint or "admin.activity_logs"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args, exclude=("page", "per_page", "reset")
    )
    if default_filters and not active_filters:
        return redirect(
            url_for(
                "admin.activity_logs",
                **filters_to_query_args(default_filters),
            )
        )

    form = ActivityLogFilterForm(meta={"csrf": False})
    user_choices = [(-1, "All Users"), (-2, "System Activity")]
    users = sorted(User.query.all(), key=lambda user: (user.sort_key, user.email.casefold()))
    user_choices.extend((user.id, user.display_label) for user in users)
    form.user_id.choices = user_choices
    form.process(request.args)
    if form.user_id.data is None:
        form.user_id.data = -1

    query = ActivityLog.query.options(selectinload(ActivityLog.user))

    user_filter = form.user_id.data
    if user_filter is not None and user_filter != -1:
        if user_filter == -2:
            query = query.filter(ActivityLog.user_id.is_(None))
        else:
            query = query.filter(ActivityLog.user_id == user_filter)

    activity_filter = (form.activity.data or "").strip()
    if activity_filter:
        query = query.filter(
            build_text_match_predicate(
                ActivityLog.activity, activity_filter, "contains"
            )
        )

    if form.start_date.data:
        start_dt = datetime.combine(form.start_date.data, datetime.min.time())
        query = query.filter(ActivityLog.timestamp >= start_dt)

    if form.end_date.data:
        end_dt = datetime.combine(form.end_date.data, datetime.max.time())
        query = query.filter(ActivityLog.timestamp <= end_dt)

    logs = query.order_by(ActivityLog.timestamp.desc()).all()
    return render_template("admin/activity_logs.html", logs=logs, form=form)


@admin.route("/controlpanel/system", methods=["GET"])
@login_required
def system_info():
    """Display runtime system information."""
    start = current_app.config.get("START_TIME")
    uptime = None
    if start:
        uptime = datetime.utcnow() - start
    try:
        version = (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"])
            .decode()
            .strip()
        )
    except Exception:
        version = "unknown"
    info = {
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "flask_version": flask.__version__,
        "version": version,
        "started_at": start,
        "uptime": str(uptime).split(".")[0] if uptime else "unknown",
    }
    return render_template("admin/system_info.html", info=info)


@admin.route("/controlpanel/imports", methods=["GET"])
@login_required
def import_page():
    """Display import options."""
    forms = {key: ImportForm(prefix=key) for key in IMPORT_FILES}
    labels = {
        "locations": "Import Locations",
        "products": "Import Products",
        "gl_codes": "Import GL Codes",
        "items": "Import Items",
        "customers": "Import Customers",
        "vendors": "Import Vendors",
        "users": "Import Users",
    }
    return render_template(
        "admin/imports.html",
        forms=forms,
        labels=labels,
        can_run_imports=current_user.has_permission("imports.run"),
    )


@admin.route(
    "/controlpanel/import/<string:data_type>/example", methods=["GET"]
)
@login_required
def download_example(data_type):
    """Download an example CSV file for the given data type."""
    from flask import current_app, send_from_directory

    if data_type not in IMPORT_FILES:
        abort(404)
    directory = current_app.config["IMPORT_FILES_FOLDER"]
    filename = IMPORT_FILES[data_type]
    log_activity(f"Downloaded example import file for {data_type}")
    return send_from_directory(directory, filename, as_attachment=True)


@admin.route("/controlpanel/import/<string:data_type>", methods=["POST"])
@login_required
def import_data(data_type):
    """Import a specific data type from an uploaded CSV file."""
    from flask import current_app

    form = ImportForm(prefix=data_type)
    if not form.validate_on_submit() or data_type not in IMPORT_FILES:
        abort(400)

    file = form.file.data
    filename = secure_filename(file.filename)
    if not filename.lower().endswith(".csv"):
        flash("Please upload a CSV file.", "error")
        return redirect(url_for("admin.import_page"))

    upload_dir = current_app.config["UPLOAD_FOLDER"]
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, filename)
    file.save(path)

    try:
        if data_type == "locations":
            count = _import_locations(path)
        elif data_type == "products":
            count = _import_products(path)
        elif data_type == "gl_codes":
            count = _import_csv(
                path, GLCode, {"code": "code", "description": "description"}
            )
        elif data_type == "items":
            count = _import_items(path)
        elif data_type == "customers":
            count = _import_csv(
                path,
                Customer,
                {
                    "first_name": "first_name",
                    "last_name": "last_name",
                },
            )
        elif data_type == "vendors":
            count = _import_csv(
                path,
                Vendor,
                {
                    "first_name": "first_name",
                    "last_name": "last_name",
                },
            )
        elif data_type == "users":
            count = _import_csv(
                path, User, {"email": "email", "password": "password"}
            )
        else:
            abort(400)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("admin.import_page"))
    finally:
        if os.path.exists(path):
            os.remove(path)
    flash(f'Imported {count} {data_type.replace("_", " ")}.', "success")
    return redirect(url_for("admin.import_page"))


@admin.route("/controlpanel/settings", methods=["GET", "POST"])
@login_required
def settings():
    """Allow admins to update application settings like GST number."""
    gst_setting = Setting.query.filter_by(name="GST").first()
    if gst_setting is None:
        gst_setting = Setting(name="GST", value="")
        db.session.add(gst_setting)

    retail_pop_price_setting = Setting.query.filter_by(
        name="RETAIL_POP_PRICE"
    ).first()
    if retail_pop_price_setting is None:
        retail_pop_price_setting = Setting(
            name="RETAIL_POP_PRICE", value="4.25"
        )
        db.session.add(retail_pop_price_setting)

    tz_setting = Setting.query.filter_by(name="DEFAULT_TIMEZONE").first()
    if tz_setting is None:
        tz_setting = Setting(name="DEFAULT_TIMEZONE", value="UTC")
        db.session.add(tz_setting)

    auto_setting = Setting.query.filter_by(name="AUTO_BACKUP_ENABLED").first()
    if auto_setting is None:
        auto_setting = Setting(name="AUTO_BACKUP_ENABLED", value="0")
        db.session.add(auto_setting)

    interval_value_setting = Setting.query.filter_by(
        name="AUTO_BACKUP_INTERVAL_VALUE"
    ).first()
    if interval_value_setting is None:
        interval_value_setting = Setting(
            name="AUTO_BACKUP_INTERVAL_VALUE", value="1"
        )
        db.session.add(interval_value_setting)

    interval_unit_setting = Setting.query.filter_by(
        name="AUTO_BACKUP_INTERVAL_UNIT"
    ).first()
    if interval_unit_setting is None:
        interval_unit_setting = Setting(
            name="AUTO_BACKUP_INTERVAL_UNIT", value="day"
        )
        db.session.add(interval_unit_setting)

    max_backups_setting = Setting.query.filter_by(name="MAX_BACKUPS").first()
    if max_backups_setting is None:
        max_backups_setting = Setting(name="MAX_BACKUPS", value="5")
        db.session.add(max_backups_setting)

    import_vendor_setting = Setting.query.filter_by(
        name=Setting.PURCHASE_IMPORT_VENDORS
    ).first()
    if import_vendor_setting is None:
        import_vendor_setting = Setting(
            name=Setting.PURCHASE_IMPORT_VENDORS,
            value=json.dumps(Setting.DEFAULT_PURCHASE_IMPORT_VENDORS),
        )
        db.session.add(import_vendor_setting)

    conversions_setting = Setting.query.filter_by(
        name="BASE_UNIT_CONVERSIONS"
    ).first()
    if conversions_setting is None:
        conversions_setting = Setting(
            name="BASE_UNIT_CONVERSIONS",
            value=serialize_conversion_setting(DEFAULT_BASE_UNIT_CONVERSIONS),
        )
        db.session.add(conversions_setting)

    db.session.commit()

    conversion_mapping = parse_conversion_setting(conversions_setting.value)
    receive_defaults = Setting.get_receive_location_defaults()
    enabled_import_vendors = Setting.get_enabled_purchase_import_vendors()
    pos_sales_import_interval = Setting.get_pos_sales_import_interval()
    settings_snapshot = {
        "gst_number": gst_setting.value or "",
        "default_timezone": tz_setting.value or "UTC",
        "auto_backup_enabled": auto_setting.value == "1",
        "auto_backup_interval": (
            int(interval_value_setting.value),
            interval_unit_setting.value,
        ),
        "pos_sales_import_interval": (
            int(pos_sales_import_interval["value"]),
            str(pos_sales_import_interval["unit"]),
        ),
        "max_backups": int(max_backups_setting.value),
        "base_unit_mapping": dict(conversion_mapping),
        "retail_pop_price": retail_pop_price_setting.value or "",
        "purchase_import_vendors": list(enabled_import_vendors),
        "receive_location_defaults": dict(receive_defaults),
    }
    retail_pop_price_value = retail_pop_price_setting.value or "0"
    try:
        retail_pop_price_decimal = Decimal(retail_pop_price_value)
    except (InvalidOperation, TypeError):
        retail_pop_price_decimal = Decimal("0")

    form = SettingsForm(
        gst_number=gst_setting.value,
        default_timezone=tz_setting.value,
        auto_backup_enabled=auto_setting.value == "1",
        auto_backup_interval_value=int(interval_value_setting.value),
        auto_backup_interval_unit=interval_unit_setting.value,
        pos_sales_import_interval_value=int(pos_sales_import_interval["value"]),
        pos_sales_import_interval_unit=str(pos_sales_import_interval["unit"]),
        max_backups=int(max_backups_setting.value),
        base_unit_mapping=conversion_mapping,
        receive_location_defaults=receive_defaults,
        purchase_import_vendors=enabled_import_vendors,
        retail_pop_price=retail_pop_price_decimal,
    )
    can_manage_settings = current_user.has_permission("settings.manage")
    if form.validate_on_submit():
        conversion_updates = {}
        has_conversion_error = False
        for unit, _, field in form.iter_base_unit_conversions():
            target = field.data
            if target not in get_allowed_target_units(unit):
                field.errors.append("Unsupported conversion selected.")
                has_conversion_error = True
            else:
                conversion_updates[unit] = target

        for unit in DEFAULT_BASE_UNIT_CONVERSIONS:
            conversion_updates.setdefault(unit, unit)

        if has_conversion_error:
            return render_template(
                "admin/settings.html",
                form=form,
                can_manage_settings=can_manage_settings,
            )

        import_vendor_fields = list(form.iter_purchase_import_vendors())
        enabled_import_vendors = [
            label for label, field in import_vendor_fields if field.data
        ]
        if not enabled_import_vendors:
            import_vendor_fields[0][1].errors.append(
                "Select at least one vendor to enable for imports."
            )
            return render_template(
                "admin/settings.html",
                form=form,
                can_manage_settings=can_manage_settings,
            )

        receive_location_updates = {}
        for department, _, field_name in PURCHASE_RECEIVE_DEPARTMENT_CONFIG:
            field = getattr(form, field_name)
            if field.data:
                receive_location_updates[department] = field.data

        new_gst_number = form.gst_number.data or ""
        new_default_timezone = form.default_timezone.data or "UTC"
        new_auto_backup_enabled = bool(form.auto_backup_enabled.data)
        new_auto_backup_interval = (
            form.auto_backup_interval_value.data,
            form.auto_backup_interval_unit.data,
        )
        new_pos_sales_import_interval = (
            form.pos_sales_import_interval_value.data,
            form.pos_sales_import_interval_unit.data,
        )
        new_max_backups = form.max_backups.data
        new_retail_pop_price = (
            ""
            if form.retail_pop_price.data is None
            else format(form.retail_pop_price.data, ".2f")
        )
        changed_settings: list[str] = []
        if settings_snapshot["gst_number"] != new_gst_number:
            changed_settings.append("GST")
        if settings_snapshot["default_timezone"] != new_default_timezone:
            changed_settings.append("default timezone")
        if settings_snapshot["auto_backup_enabled"] != new_auto_backup_enabled:
            changed_settings.append("auto backup enabled")
        if settings_snapshot["auto_backup_interval"] != new_auto_backup_interval:
            changed_settings.append("auto backup cadence")
        if (
            settings_snapshot["pos_sales_import_interval"]
            != new_pos_sales_import_interval
        ):
            changed_settings.append("POS sales import cadence")
        if settings_snapshot["max_backups"] != new_max_backups:
            changed_settings.append("max backups")
        if settings_snapshot["base_unit_mapping"] != conversion_updates:
            changed_settings.append("base unit conversions")
        if settings_snapshot["retail_pop_price"] != new_retail_pop_price:
            changed_settings.append("retail pop price")
        if settings_snapshot["purchase_import_vendors"] != enabled_import_vendors:
            changed_settings.append("purchase import vendors")
        if (
            settings_snapshot["receive_location_defaults"]
            != receive_location_updates
        ):
            changed_settings.append("receive location defaults")

        gst_setting.value = new_gst_number
        tz_setting.value = new_default_timezone
        auto_setting.value = "1" if new_auto_backup_enabled else "0"
        interval_value_setting.value = str(
            form.auto_backup_interval_value.data
        )
        interval_unit_setting.value = form.auto_backup_interval_unit.data
        max_backups_setting.value = str(new_max_backups)
        conversions_setting.value = serialize_conversion_setting(
            conversion_updates
        )
        retail_pop_price_setting.value = new_retail_pop_price
        Setting.set_pos_sales_import_interval(
            value=form.pos_sales_import_interval_value.data,
            unit=form.pos_sales_import_interval_unit.data,
        )
        Setting.set_enabled_purchase_import_vendors(enabled_import_vendors)
        Setting.set_receive_location_defaults(receive_location_updates)
        db.session.commit()
        import app

        app.GST = gst_setting.value
        app.RETAIL_POP_PRICE = (
            retail_pop_price_setting.value or "0.00"
        )
        app.DEFAULT_TIMEZONE = tz_setting.value
        current_app.config["AUTO_BACKUP_ENABLED"] = (
            form.auto_backup_enabled.data
        )
        current_app.config["AUTO_BACKUP_INTERVAL_VALUE"] = (
            form.auto_backup_interval_value.data
        )
        current_app.config["AUTO_BACKUP_INTERVAL_UNIT"] = (
            form.auto_backup_interval_unit.data
        )
        current_app.config["POS_SALES_IMPORT_INTERVAL_VALUE"] = (
            form.pos_sales_import_interval_value.data
        )
        current_app.config["POS_SALES_IMPORT_INTERVAL_UNIT"] = (
            form.pos_sales_import_interval_unit.data
        )
        current_app.config["MAX_BACKUPS"] = form.max_backups.data
        current_app.config["AUTO_BACKUP_INTERVAL"] = (
            form.auto_backup_interval_value.data
            * UNIT_SECONDS[form.auto_backup_interval_unit.data]
        )
        current_app.config["RETAIL_POP_PRICE"] = app.RETAIL_POP_PRICE
        conversion_mapping = parse_conversion_setting(conversions_setting.value)
        app.BASE_UNIT_CONVERSIONS = conversion_mapping
        current_app.config["BASE_UNIT_CONVERSIONS"] = conversion_mapping
        start_auto_backup_thread(current_app._get_current_object())
        if changed_settings:
            log_activity(
                "Updated settings: "
                + ", ".join(dict.fromkeys(changed_settings))
            )
        flash("Settings updated.", "success")
        return redirect(url_for("admin.settings"))

    return render_template(
        "admin/settings.html",
        form=form,
        can_manage_settings=can_manage_settings,
    )


@admin.route("/controlpanel/terminal-sales-mappings", methods=["GET"])
@login_required
def terminal_sales_mappings():
    """Redirect legacy terminal mapping bookmarks to the locations area."""
    flash(
        "Terminal sales location mappings now live on each location page.",
        "info",
    )
    target_endpoint = (
        "locations.view_locations"
        if current_user.can_access_endpoint("locations.view_locations", "GET")
        else get_default_landing_endpoint(current_user)
    )
    return redirect(url_for(target_endpoint))


@admin.route("/controlpanel/sales-imports", methods=["GET", "POST"])
@login_required
def sales_imports():
    """Render staged POS sales imports for admin review."""
    scope = request.endpoint or "admin.sales_imports"
    default_filters = get_filter_defaults(current_user, scope)
    active_filters = normalize_filters(
        request.args, exclude=("page", "per_page", "reset")
    )
    if default_filters and not active_filters:
        return redirect(
            url_for("admin.sales_imports", **filters_to_query_args(default_filters))
        )

    available_statuses = [
        "pending",
        "approved",
        "ignored",
    ]
    page = request.args.get("page", 1, type=int)
    per_page = get_per_page()
    search_query = (request.args.get("search") or "").strip()
    status_filter = (request.args.get("status") or "").strip().lower()
    if status_filter not in available_statuses:
        status_filter = ""
    if request.method == "POST":
        search_query = (request.form.get("search") or search_query).strip()
        status_filter = (request.form.get("status") or status_filter).strip().lower()
        if status_filter not in available_statuses:
            status_filter = ""
        action = (request.form.get("action") or "").strip()
        if action == "approve_import":
            import_id = request.form.get("import_id", type=int)
            if not import_id:
                flash("Unable to find the selected sales import.", "danger")
            else:
                _approve_sales_import(import_id)
        redirect_args: dict[str, str] = {}
        if status_filter:
            redirect_args["status"] = status_filter
        if search_query:
            redirect_args["search"] = search_query
        redirect_per_page = (request.form.get("per_page") or "").strip()
        if redirect_per_page:
            redirect_args["per_page"] = redirect_per_page
        redirect_page = (request.form.get("page") or "").strip()
        if redirect_page and redirect_page != "1":
            redirect_args["page"] = redirect_page
        return redirect(url_for("admin.sales_imports", **redirect_args))

    query = PosSalesImport.query.filter(
        PosSalesImport.status != "deleted"
    ).options(
        selectinload(PosSalesImport.locations)
        .selectinload(PosSalesImportLocation.rows)
        .selectinload(PosSalesImportRow.product),
        selectinload(PosSalesImport.rows).selectinload(PosSalesImportRow.product),
    ).order_by(
        PosSalesImport.received_at.desc(),
        PosSalesImport.id.desc(),
    )
    if status_filter:
        query = query.filter(PosSalesImport.status == status_filter)
    if search_query:
        query = query.filter(
            or_(
                build_text_match_predicate(
                    PosSalesImport.attachment_filename, search_query, "contains"
                ),
                build_text_match_predicate(
                    PosSalesImport.message_id, search_query, "contains"
                ),
                build_text_match_predicate(
                    PosSalesImport.source_provider, search_query, "contains"
                ),
            )
        )

    imports = query.paginate(page=page, per_page=per_page)
    status_changed = False
    for import_record in imports.items:
        assignment_changed = _sync_sales_import_event_assignments(import_record)
        issue_state = _refresh_sales_import_mapping_status(import_record)
        status_changed = (
            status_changed or assignment_changed or issue_state["status_changed"]
        )
        actionable_issue_count = (
            issue_state["issue_count"]
            if import_record.status in _SALES_IMPORT_REVIEW_EDITABLE_STATUSES
            else 0
        )
        import_record.issue_count = actionable_issue_count
        import_record.needs_mapping = issue_state["needs_mapping"]
        import_record.can_direct_approve = (
            _sales_import_can_be_approved(import_record)
            and actionable_issue_count == 0
        )
        import_record.attachment_available = (
            _get_sales_import_attachment_path(import_record) is not None
        )

    if status_changed:
        db.session.commit()

    return render_template(
        "admin/sales_imports.html",
        imports=imports,
        status_filter=status_filter,
        search_query=search_query,
        available_statuses=available_statuses,
        per_page=per_page,
        pagination_args=build_pagination_args(per_page),
    )


def _parse_sales_import_approval_changes(row: PosSalesImportRow) -> list[dict]:
    payload = _parse_sales_import_row_metadata(row)
    if not payload:
        return []
    changes = payload.get("changes") or []
    if not isinstance(changes, list):
        return []
    return [change for change in changes if isinstance(change, dict)]


_SALES_IMPORT_PRICE_ACTIONS = {"file", "app", "custom", "skip"}
_SALES_IMPORT_LOCATION_FILTERS = {"all", "issues"}
_SALES_IMPORT_REVIEW_EDITABLE_STATUSES = {
    PosSalesImport.STATUS_PENDING,
}
_SALES_IMPORT_APPROVABLE_STATUSES = {
    PosSalesImport.STATUS_PENDING,
}


def _sales_import_review_is_editable(import_record: PosSalesImport) -> bool:
    return import_record.status in _SALES_IMPORT_REVIEW_EDITABLE_STATUSES


def _sales_import_can_be_approved(import_record: PosSalesImport) -> bool:
    return import_record.status in _SALES_IMPORT_APPROVABLE_STATUSES


def _sales_import_review_locked_message(import_record: PosSalesImport) -> str:
    if import_record.status == PosSalesImport.STATUS_APPROVED:
        return "Undo the approved import before changing review mappings or prices."
    if import_record.status == PosSalesImport.STATUS_DELETED:
        return "Deleted imports cannot be changed."
    return "Review changes are only allowed while the import is pending approval."


def _parse_sales_import_row_metadata(row: PosSalesImportRow) -> dict[str, Any]:
    if not row.approval_metadata:
        return {}
    try:
        payload = json.loads(row.approval_metadata)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_sales_import_row_metadata(
    row: PosSalesImportRow, payload: dict[str, Any]
) -> None:
    row.approval_metadata = json.dumps(payload) if payload else None


def _parse_sales_import_location_metadata(
    location_import: PosSalesImportLocation,
) -> dict[str, Any]:
    if not location_import.approval_metadata:
        return {}
    try:
        payload = json.loads(location_import.approval_metadata)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _write_sales_import_location_metadata(
    location_import: PosSalesImportLocation, payload: dict[str, Any]
) -> None:
    location_import.approval_metadata = json.dumps(payload) if payload else None


def _get_sales_import_location_review(
    location_import: PosSalesImportLocation,
) -> dict[str, Any]:
    payload = _parse_sales_import_location_metadata(location_import)
    review = payload.get("review")
    if not isinstance(review, dict):
        return {}
    return review


def _sales_import_location_is_skipped(
    location_import: PosSalesImportLocation,
) -> bool:
    return bool(_get_sales_import_location_review(location_import).get("skip"))


def _normalize_sales_import_location_filter(raw_value: Any) -> str:
    location_filter = (raw_value or "").strip().lower()
    if location_filter in _SALES_IMPORT_LOCATION_FILTERS:
        return location_filter
    return "all"


def _format_sales_import_event_label(event_location: EventLocation) -> str:
    event_obj = event_location.event
    location_obj = event_location.location
    if event_obj is None:
        return location_obj.name if location_obj is not None else f"Event location #{event_location.id}"
    date_label = (
        event_obj.start_date.isoformat()
        if event_obj.start_date == event_obj.end_date
        else f"{event_obj.start_date.isoformat()} to {event_obj.end_date.isoformat()}"
    )
    location_label = location_obj.name if location_obj is not None else "Unknown location"
    return f"{event_obj.name} | {location_label} | {date_label}"


def _load_sales_import_event_candidates(
    sales_import: PosSalesImport,
) -> dict[int, list[EventLocation]]:
    candidate_lookup: dict[int, list[EventLocation]] = {}
    if sales_import.sales_date is None:
        return candidate_lookup

    location_ids = sorted(
        {
            import_location.location_id
            for import_location in sales_import.locations
            if import_location.location_id is not None
        }
    )
    if not location_ids:
        return candidate_lookup

    candidate_rows = (
        EventLocation.query.options(
            selectinload(EventLocation.event),
            selectinload(EventLocation.location),
        )
        .join(Event, Event.id == EventLocation.event_id)
        .filter(EventLocation.location_id.in_(location_ids))
        .filter(Event.closed.is_(False))
        .filter(Event.start_date <= sales_import.sales_date)
        .filter(Event.end_date >= sales_import.sales_date)
        .order_by(
            EventLocation.location_id.asc(),
            Event.start_date.asc(),
            Event.end_date.asc(),
            Event.id.asc(),
        )
        .all()
    )

    for candidate in candidate_rows:
        candidate_lookup.setdefault(candidate.location_id, []).append(candidate)
    return candidate_lookup


def _sync_sales_import_event_assignments(
    sales_import: PosSalesImport,
    *,
    candidate_lookup: dict[int, list[EventLocation]] | None = None,
) -> bool:
    if sales_import.status not in _SALES_IMPORT_REVIEW_EDITABLE_STATUSES:
        return False

    candidate_lookup = (
        candidate_lookup
        if candidate_lookup is not None
        else _load_sales_import_event_candidates(sales_import)
    )
    changed = False

    for import_location in sales_import.locations:
        if (
            _sales_import_location_is_skipped(import_location)
            or import_location.location_id is None
            or sales_import.sales_date is None
        ):
            if import_location.event_location_id is not None:
                import_location.event_location_id = None
                import_location.event_location = None
                changed = True
            continue

        candidates = candidate_lookup.get(import_location.location_id, [])
        if len(candidates) != 1:
            if import_location.event_location_id is not None:
                import_location.event_location_id = None
                import_location.event_location = None
                changed = True
            continue

        matched_event_location = candidates[0]
        candidate_id = matched_event_location.id
        if import_location.event_location_id != candidate_id:
            import_location.event_location_id = candidate_id
            changed = True
        if import_location.event_location is not matched_event_location:
            import_location.event_location = matched_event_location

    return changed


def _build_sales_import_event_assignment_state(
    sales_import: PosSalesImport,
    *,
    candidate_lookup: dict[int, list[EventLocation]] | None = None,
) -> dict[str, Any]:
    candidate_lookup = (
        candidate_lookup
        if candidate_lookup is not None
        else _load_sales_import_event_candidates(sales_import)
    )

    unresolved_event_location_ids: set[int] = set()
    conflicting_event_location_ids: set[int] = set()
    direct_inventory_only_location_ids: set[int] = set()
    candidate_event_locations_by_import_location: dict[int, list[EventLocation]] = {}
    event_assignment_messages: dict[int, list[str]] = {}

    for import_location in sales_import.locations:
        messages: list[str] = []
        if _sales_import_location_is_skipped(import_location):
            candidate_event_locations_by_import_location[import_location.id] = []
            messages.append(
                "This import location is skipped. Approval will ignore its event routing and inventory rows."
            )
            event_assignment_messages[import_location.id] = messages
            continue

        candidates = (
            candidate_lookup.get(import_location.location_id, [])
            if import_location.location_id is not None
            else []
        )
        candidate_event_locations_by_import_location[import_location.id] = candidates

        if import_location.location_id is None:
            event_assignment_messages[import_location.id] = messages
            continue

        if sales_import.sales_date is None:
            unresolved_event_location_ids.add(import_location.id)
            messages.append(
                "Sales date is not set. Save the sales date before approval so the app can determine whether this location belongs to an event."
            )
            event_assignment_messages[import_location.id] = messages
            continue

        if not candidates:
            direct_inventory_only_location_ids.add(import_location.id)
            messages.append(
                "No open event matches this location on the sales date. Approval will apply directly to location inventory."
            )
            event_assignment_messages[import_location.id] = messages
            continue

        if len(candidates) > 1:
            unresolved_event_location_ids.add(import_location.id)
            conflicting_event_location_ids.add(import_location.id)
            messages.append(
                "Multiple open events match this location and sales date. Imported sales cannot be split without timestamps, so combine or fix those events before approval."
            )
            event_assignment_messages[import_location.id] = messages
            continue

        selected_event = candidates[0]
        if import_location.event_location_id != selected_event.id:
            unresolved_event_location_ids.add(import_location.id)
            messages.append(
                "A matching event was found, but the assignment has not been synced yet. Refresh the page or save another mapping change before approval."
            )
        else:
            messages.append(
                f"Sales will post to {_format_sales_import_event_label(selected_event)}."
            )

        event_assignment_messages[import_location.id] = messages

    return {
        "candidate_event_locations_by_import_location": candidate_event_locations_by_import_location,
        "conflicting_event_location_ids": conflicting_event_location_ids,
        "direct_inventory_only_location_ids": direct_inventory_only_location_ids,
        "event_assignment_messages": event_assignment_messages,
        "unresolved_event_location_ids": unresolved_event_location_ids,
    }


def _serialize_event_location_sales_state(
    event_location: EventLocation | None,
) -> dict[str, Any]:
    if event_location is None:
        return {"terminal_sales": [], "summary": None}

    terminal_sales = []
    for sale in event_location.terminal_sales:
        terminal_sales.append(
            {
                "product_id": sale.product_id,
                "quantity": float(sale.quantity or 0.0),
                "sold_at": sale.sold_at.isoformat() if sale.sold_at else None,
            }
        )

    summary_payload = None
    if event_location.terminal_sales_summary is not None:
        summary_payload = {
            "source_location": event_location.terminal_sales_summary.source_location,
            "total_quantity": event_location.terminal_sales_summary.total_quantity,
            "total_amount": event_location.terminal_sales_summary.total_amount,
            "variance_details": event_location.terminal_sales_summary.variance_details,
        }

    return {
        "terminal_sales": terminal_sales,
        "summary": summary_payload,
    }


def _restore_event_location_sales_state(
    event_location_id: int,
    snapshot: dict[str, Any] | None,
) -> None:
    TerminalSale.query.filter_by(event_location_id=event_location_id).delete(
        synchronize_session=False
    )
    EventLocationTerminalSalesSummary.query.filter_by(
        event_location_id=event_location_id
    ).delete(synchronize_session=False)
    db.session.flush()

    if not snapshot:
        return

    for sale_payload in snapshot.get("terminal_sales", []):
        sold_at_value = sale_payload.get("sold_at")
        sold_at = None
        if isinstance(sold_at_value, str):
            try:
                sold_at = datetime.fromisoformat(sold_at_value)
            except ValueError:
                sold_at = None
        db.session.add(
            TerminalSale(
                event_location_id=event_location_id,
                product_id=sale_payload.get("product_id"),
                quantity=float(sale_payload.get("quantity") or 0.0),
                sold_at=sold_at or datetime.utcnow(),
            )
        )

    summary_payload = snapshot.get("summary")
    if isinstance(summary_payload, dict):
        db.session.add(
            EventLocationTerminalSalesSummary(
                event_location_id=event_location_id,
                source_location=summary_payload.get("source_location"),
                total_quantity=coerce_float(summary_payload.get("total_quantity")),
                total_amount=coerce_float(summary_payload.get("total_amount")),
                variance_details=summary_payload.get("variance_details"),
            )
        )


def _empty_event_linked_variance_details() -> dict[str, list[dict[str, Any]]]:
    return {
        "products": [],
        "price_mismatches": [],
        "menu_issues": [],
        "unmapped_products": [],
    }


def _normalize_event_linked_variance_details(
    value: Any,
) -> dict[str, list[dict[str, Any]]]:
    normalized = _empty_event_linked_variance_details()
    if not isinstance(value, dict):
        return normalized
    for key in normalized:
        entries = value.get(key)
        if isinstance(entries, list):
            normalized[key] = [entry for entry in entries if isinstance(entry, dict)]
    return normalized


def _event_linked_variance_details_has_entries(value: Any) -> bool:
    details = _normalize_event_linked_variance_details(value)
    return any(details[key] for key in details)


def _build_event_linked_sales_summary_contribution(
    import_locations: list[PosSalesImportLocation],
) -> dict[str, Any]:
    source_location_names: list[str] = []
    skipped_rows: list[dict[str, Any]] = []
    total_quantity = 0.0
    total_amount = 0.0

    for import_location in import_locations:
        source_name = (import_location.source_location_name or "").strip()
        if source_name and source_name not in source_location_names:
            source_location_names.append(source_name)

        total_quantity += float(import_location.total_quantity or 0.0)
        total_amount += float(import_location.computed_total or 0.0)

        for row in import_location.rows:
            review = _get_sales_import_row_review(row)
            action = _normalize_sales_import_price_action(review.get("price_action"))
            if action != "skip":
                continue
            file_price = coerce_float(row.computed_unit_price)
            skipped_rows.append(
                {
                    "product_name": row.source_product_name,
                    "quantity": float(row.quantity or 0.0),
                    "file_amount": float(row.computed_line_total or 0.0),
                    "file_prices": [file_price] if file_price is not None else [],
                    "sales_location": source_name or None,
                }
            )

    variance_details = None
    if skipped_rows:
        variance_details = _empty_event_linked_variance_details()
        variance_details["unmapped_products"] = skipped_rows

    return {
        "source_location": ", ".join(source_location_names) or None,
        "total_quantity": total_quantity,
        "total_amount": total_amount,
        "variance_details": variance_details,
    }


def _split_event_linked_source_locations(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    seen: set[str] = set()
    names: list[str] = []
    for chunk in value.split(","):
        normalized = chunk.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        names.append(normalized)
    return names


def _event_location_has_remaining_approved_source_name(
    event_location_id: int,
    source_name: str,
    *,
    excluded_import_id: int | None = None,
) -> bool:
    query = (
        PosSalesImportLocation.query.join(
            PosSalesImport, PosSalesImport.id == PosSalesImportLocation.import_id
        )
        .filter(PosSalesImportLocation.event_location_id == event_location_id)
        .filter(PosSalesImport.status == PosSalesImport.STATUS_APPROVED)
        .filter(PosSalesImportLocation.source_location_name == source_name)
    )
    if excluded_import_id is not None:
        query = query.filter(PosSalesImportLocation.import_id != excluded_import_id)
    return query.first() is not None


def _apply_event_linked_summary_delta(
    event_location_id: int,
    contribution: dict[str, Any],
    *,
    mode: str,
    excluded_import_id: int | None = None,
) -> None:
    summary_record = EventLocationTerminalSalesSummary.query.filter_by(
        event_location_id=event_location_id
    ).first()
    contribution_quantity = float(contribution.get("total_quantity") or 0.0)
    contribution_amount = float(contribution.get("total_amount") or 0.0)
    contribution_names = _split_event_linked_source_locations(
        contribution.get("source_location")
    )
    contribution_details = _normalize_event_linked_variance_details(
        contribution.get("variance_details")
    )

    if summary_record is None:
        if mode == "subtract":
            return
        summary_record = EventLocationTerminalSalesSummary(
            event_location_id=event_location_id
        )
        db.session.add(summary_record)

    existing_quantity = float(summary_record.total_quantity or 0.0)
    existing_amount = float(summary_record.total_amount or 0.0)
    existing_names = _split_event_linked_source_locations(summary_record.source_location)
    existing_details = _normalize_event_linked_variance_details(
        summary_record.variance_details
    )

    if mode == "add":
        updated_quantity = existing_quantity + contribution_quantity
        updated_amount = existing_amount + contribution_amount
        for name in contribution_names:
            if name not in existing_names:
                existing_names.append(name)
        for key in existing_details:
            existing_details[key].extend(contribution_details[key])
    else:
        updated_quantity = existing_quantity - contribution_quantity
        updated_amount = existing_amount - contribution_amount
        retained_names: list[str] = []
        for name in existing_names:
            if (
                name in contribution_names
                and not _event_location_has_remaining_approved_source_name(
                    event_location_id,
                    name,
                    excluded_import_id=excluded_import_id,
                )
            ):
                continue
            if name not in retained_names:
                retained_names.append(name)
        existing_names = retained_names
        for key in existing_details:
            for entry in contribution_details[key]:
                if entry in existing_details[key]:
                    existing_details[key].remove(entry)

    if abs(updated_quantity) < 1e-9:
        updated_quantity = 0.0
    if abs(updated_amount) < 1e-9:
        updated_amount = 0.0

    has_details = _event_linked_variance_details_has_entries(existing_details)
    if (
        not existing_names
        and not has_details
        and abs(updated_quantity) < 1e-9
        and abs(updated_amount) < 1e-9
    ):
        db.session.delete(summary_record)
        return

    summary_record.source_location = ", ".join(existing_names) or None
    summary_record.total_quantity = updated_quantity
    summary_record.total_amount = updated_amount
    summary_record.variance_details = existing_details if has_details else None


def _build_event_linked_sales_application_payload(
    sales_import: PosSalesImport,
    import_locations: list[PosSalesImportLocation],
    row_review_data: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    aggregated_sales: dict[int, dict[str, Any]] = {}
    sales_date_value = sales_import.sales_date
    sold_at = (
        datetime.combine(sales_date_value, time(hour=12))
        if isinstance(sales_date_value, date_cls)
        else datetime.utcnow()
    )

    for import_location in import_locations:
        for row in import_location.rows:
            row_review = row_review_data.get(row.id, {})
            if row_review.get("is_active") and row.product_id is not None:
                payload = aggregated_sales.setdefault(
                    row.product_id,
                    {
                        "product_id": row.product_id,
                        "quantity": 0.0,
                        "sold_at": sold_at,
                    },
                )
                payload["quantity"] += float(row.quantity or 0.0)
                continue

    return {
        "terminal_sales": list(aggregated_sales.values()),
        "summary": _build_event_linked_sales_summary_contribution(import_locations),
    }


def _apply_event_linked_sales_payload(
    event_location: EventLocation,
    payload: dict[str, Any],
    *,
    source_import_id: int,
    approval_batch_id: str,
) -> None:
    TerminalSale.query.filter_by(
        event_location_id=event_location.id,
        approval_batch_id=approval_batch_id,
    ).delete(synchronize_session=False)
    db.session.flush()

    location_obj = event_location.location
    for sale_payload in payload.get("terminal_sales", []):
        product_id = sale_payload.get("product_id")
        if product_id is None:
            continue
        product = db.session.get(Product, product_id)
        if product is None:
            continue
        if (
            location_obj is not None
            and location_obj.current_menu is None
            and product not in location_obj.products
        ):
            location_obj.products.append(product)
            sync_location_stand_items(
                location_obj,
                products=[product],
                remove_missing=False,
            )
        db.session.add(
            TerminalSale(
                event_location_id=event_location.id,
                product_id=product.id,
                pos_sales_import_id=source_import_id,
                approval_batch_id=approval_batch_id,
                quantity=float(sale_payload.get("quantity") or 0.0),
                sold_at=sale_payload.get("sold_at") or datetime.utcnow(),
            )
        )


def _normalize_sales_import_price_action(raw_value: Any) -> str | None:
    action = (raw_value or "").strip().lower()
    if action in _SALES_IMPORT_PRICE_ACTIONS:
        return action
    return None


def _get_sales_import_row_review(row: PosSalesImportRow) -> dict[str, Any]:
    payload = _parse_sales_import_row_metadata(row)
    review = payload.get("review")
    if not isinstance(review, dict):
        return {}
    return review


def _sales_import_prices_match(
    file_price: float | None, app_price: float | None
) -> bool:
    if file_price is None or app_price is None:
        return False
    try:
        return abs(float(file_price) - float(app_price)) <= 0.01
    except (TypeError, ValueError):
        return False


def _sales_import_discount_value(
    discount_raw: Any, discount_abs: float | None = None
) -> float:
    parsed_value = coerce_float(discount_raw)
    if parsed_value is not None:
        return parsed_value
    fallback_value = coerce_float(discount_abs, default=0.0)
    return fallback_value or 0.0


def _build_sales_import_review_context(
    sales_import: PosSalesImport,
) -> dict[str, Any]:
    location_review_data: dict[int, dict[str, Any]] = {}
    row_review_data: dict[int, dict[str, Any]] = {}
    location_discount_totals: dict[int, float] = {}
    unresolved_location_ids: set[int] = set()
    unresolved_row_ids: set[int] = set()
    unresolved_price_row_ids: set[int] = set()
    grouped_product_prices: dict[int, list[tuple[int, float]]] = {}

    import_discount_total = 0.0

    for location in sales_import.locations:
        location_review = _get_sales_import_location_review(location)
        location_is_skipped = bool(location_review.get("skip"))
        location_review_data[location.id] = {
            "is_skipped": location_is_skipped,
        }
        location_discount_total = 0.0
        location_has_active_rows = False

        for row in location.rows:
            review = _get_sales_import_row_review(row)
            action = _normalize_sales_import_price_action(review.get("price_action"))
            custom_price = coerce_float(review.get("selected_price"))
            file_price = coerce_float(row.computed_unit_price, default=0.0)
            app_price = (
                coerce_float(row.product.price)
                if row.product is not None
                else None
            )
            row_discount = _sales_import_discount_value(
                row.discount_raw, row.discount_abs
            )
            is_skipped = action == "skip"
            is_active = (
                not location_is_skipped and not row.is_zero_quantity and not is_skipped
            )
            location_discount_total += row_discount

            price_mismatch = bool(
                row.product is not None
                and is_active
                and not _sales_import_prices_match(file_price, app_price)
            )

            resolved_price = None
            resolved_source = None
            if action == "file":
                resolved_price = file_price
                resolved_source = "file"
            elif action == "app":
                resolved_price = app_price
                resolved_source = "app"
            elif action == "custom" and custom_price is not None:
                resolved_price = custom_price
                resolved_source = "custom"
            elif action == "skip":
                resolved_source = "skip"
            elif row.product is not None and not price_mismatch:
                resolved_price = app_price
                resolved_source = "aligned"

            requires_mapping = bool(is_active and row.product_id is None)
            requires_price_resolution = bool(
                row.product is not None and is_active and price_mismatch and resolved_price is None
            )

            if is_active:
                location_has_active_rows = True
            if requires_mapping:
                unresolved_row_ids.add(row.id)
            if requires_price_resolution:
                unresolved_price_row_ids.add(row.id)
            if (
                is_active
                and row.product_id is not None
                and resolved_price is not None
            ):
                grouped_product_prices.setdefault(row.product_id, []).append(
                    (row.id, resolved_price)
                )

            row_review_data[row.id] = {
                "action": action,
                "custom_price": custom_price,
                "file_price": file_price,
                "app_price": app_price,
                "discount": row_discount,
                "is_skipped": is_skipped,
                "is_active": is_active,
                "location_is_skipped": location_is_skipped,
                "price_mismatch": price_mismatch,
                "resolved_price": resolved_price,
                "resolved_source": resolved_source,
                "requires_mapping": requires_mapping,
                "requires_price_resolution": requires_price_resolution,
                "has_price_conflict": False,
            }

        location_discount_totals[location.id] = location_discount_total
        import_discount_total += location_discount_total
        if location.location_id is None and location_has_active_rows:
            unresolved_location_ids.add(location.id)

    conflicting_row_ids: set[int] = set()
    for grouped_rows in grouped_product_prices.values():
        if len(grouped_rows) < 2:
            continue
        baseline_price = grouped_rows[0][1]
        if any(
            not _sales_import_prices_match(price, baseline_price)
            for _, price in grouped_rows[1:]
        ):
            conflicting_row_ids.update(row_id for row_id, _ in grouped_rows)

    for row_id in conflicting_row_ids:
        row_review_data[row_id]["has_price_conflict"] = True
        unresolved_price_row_ids.add(row_id)

    return {
        "location_review_data": location_review_data,
        "row_review_data": row_review_data,
        "location_discount_totals": location_discount_totals,
        "import_discount_total": import_discount_total,
        "unresolved_location_ids": unresolved_location_ids,
        "unresolved_row_ids": unresolved_row_ids,
        "unresolved_price_row_ids": unresolved_price_row_ids,
    }


def _collect_sales_import_issue_state(
    import_record: PosSalesImport,
) -> dict[str, Any]:
    review_context = _build_sales_import_review_context(import_record)
    event_assignment_state = _build_sales_import_event_assignment_state(
        import_record
    )
    unresolved_location_count = len(review_context["unresolved_location_ids"])
    unresolved_row_count = len(review_context["unresolved_row_ids"])
    unresolved_price_count = len(review_context["unresolved_price_row_ids"])
    unresolved_event_location_count = len(
        event_assignment_state["unresolved_event_location_ids"]
    )
    errors: list[str] = []
    if unresolved_location_count:
        errors.append(
            f"{unresolved_location_count} import location"
            f"{'s are' if unresolved_location_count != 1 else ' is'} unresolved."
        )
    if unresolved_event_location_count:
        errors.append(
            f"{unresolved_event_location_count} import location"
            f"{'s need' if unresolved_event_location_count != 1 else ' needs'} event assignment."
        )
    if unresolved_row_count:
        errors.append(
            f"{unresolved_row_count} import row"
            f"{'s are' if unresolved_row_count != 1 else ' is'} unresolved."
        )
    if unresolved_price_count:
        errors.append(
            f"{unresolved_price_count} import row"
            f"{'s need' if unresolved_price_count != 1 else ' needs'} price review."
        )
    return {
        "review_context": review_context,
        "event_assignment_state": event_assignment_state,
        "unresolved_location_count": unresolved_location_count,
        "unresolved_event_location_count": unresolved_event_location_count,
        "unresolved_row_count": unresolved_row_count,
        "unresolved_price_count": unresolved_price_count,
        "issue_count": (
            unresolved_location_count
            + unresolved_event_location_count
            + unresolved_row_count
            + unresolved_price_count
        ),
        "errors": errors,
    }


def _refresh_sales_import_mapping_status(
    import_record: PosSalesImport,
) -> dict[str, Any]:
    issue_state = _collect_sales_import_issue_state(import_record)
    issue_state["needs_mapping"] = bool(
        issue_state["unresolved_location_count"]
        or issue_state["unresolved_event_location_count"]
        or issue_state["unresolved_row_count"]
    )
    issue_state["status_changed"] = False
    return issue_state


def _get_sales_import_attachment_path(
    import_record: PosSalesImport,
) -> str | None:
    attachment_path = (import_record.attachment_storage_path or "").strip()
    if not attachment_path:
        return None

    normalized_path = os.path.abspath(attachment_path)
    if not os.path.isfile(normalized_path):
        return None
    return normalized_path


def _detach_sales_import_attachment(import_record: PosSalesImport) -> None:
    attachment_path = (import_record.attachment_storage_path or "").strip()
    import_record.attachment_storage_path = None
    if not attachment_path:
        return

    active_reference_exists = (
        PosSalesImport.query.with_entities(PosSalesImport.id)
        .filter(
            PosSalesImport.id != import_record.id,
            PosSalesImport.attachment_storage_path == attachment_path,
            PosSalesImport.status != "deleted",
        )
        .first()
        is not None
    )
    if active_reference_exists:
        return

    deleted_imports = PosSalesImport.query.filter(
        PosSalesImport.attachment_storage_path == attachment_path,
        PosSalesImport.status == "deleted",
    ).all()
    for deleted_import in deleted_imports:
        deleted_import.attachment_storage_path = None

    if os.path.exists(attachment_path):
        os.remove(attachment_path)


def _approve_sales_import(import_id: int) -> bool:
    try:
        locked_import = (
            PosSalesImport.query.filter(PosSalesImport.id == import_id)
            .with_for_update()
            .first()
        )
        if locked_import is None:
            flash("The requested import could not be found.", "danger")
            return False

        locked_import = (
            PosSalesImport.query.options(
                selectinload(PosSalesImport.locations)
                .selectinload(PosSalesImportLocation.rows)
                .selectinload(PosSalesImportRow.product)
                .selectinload(Product.recipe_items)
                .selectinload(ProductRecipeItem.unit),
                selectinload(PosSalesImport.locations)
                .selectinload(PosSalesImportLocation.rows)
                .selectinload(PosSalesImportRow.product)
                .selectinload(Product.recipe_items)
                .selectinload(ProductRecipeItem.item),
                selectinload(PosSalesImport.locations).selectinload(
                    PosSalesImportLocation.location
                ),
                selectinload(PosSalesImport.locations)
                .selectinload(PosSalesImportLocation.event_location)
                .selectinload(EventLocation.event),
                selectinload(PosSalesImport.locations)
                .selectinload(PosSalesImportLocation.event_location)
                .selectinload(EventLocation.location),
                selectinload(PosSalesImport.locations)
                .selectinload(PosSalesImportLocation.event_location)
                .selectinload(EventLocation.terminal_sales),
                selectinload(PosSalesImport.locations)
                .selectinload(PosSalesImportLocation.event_location)
                .selectinload(EventLocation.terminal_sales_summary),
            )
            .filter(PosSalesImport.id == import_id)
            .first()
        )
        if locked_import is None:
            flash("The requested import could not be found.", "danger")
            return False

        if locked_import.status in _SALES_IMPORT_REVIEW_EDITABLE_STATUSES:
            _sync_sales_import_event_assignments(locked_import)
            issue_state = _refresh_sales_import_mapping_status(locked_import)
        else:
            issue_state = _collect_sales_import_issue_state(locked_import)

        if not _sales_import_can_be_approved(locked_import):
            flash(
                "Import approval is only allowed while the import status is Pending.",
                "warning",
            )
            return False

        if issue_state["errors"]:
            flash(
                "Approval blocked: resolve mappings and price review issues before approval.",
                "warning",
            )
            for error in issue_state["errors"]:
                flash(error, "warning")
            return False

        approval_batch_id = f"pos-import-{locked_import.id}-{uuid.uuid4().hex[:12]}"
        approval_time = datetime.utcnow()
        row_change_count = 0
        row_review_data = issue_state["review_context"]["row_review_data"]
        product_price_updates: dict[int, float] = {}

        for row in locked_import.rows:
            row_review = row_review_data.get(row.id, {})
            if (
                not row_review.get("is_active")
                or row.product_id is None
                or row_review.get("resolved_price") is None
            ):
                continue
            product_price_updates[row.product_id] = row_review["resolved_price"]

        for product_id, selected_price in product_price_updates.items():
            product = db.session.get(Product, product_id)
            if product is None:
                continue
            product.price = selected_price

        event_linked_groups: dict[int, list[PosSalesImportLocation]] = {}
        for import_location in locked_import.locations:
            if import_location.event_location_id is None:
                continue
            event_linked_groups.setdefault(
                import_location.event_location_id, []
            ).append(import_location)

        for event_location_id, grouped_locations in event_linked_groups.items():
            event_location = next(
                (
                    location.event_location
                    for location in grouped_locations
                    if location.event_location is not None
                ),
                None,
            )
            if event_location is None:
                continue

            payload = _build_event_linked_sales_application_payload(
                locked_import,
                grouped_locations,
                row_review_data,
            )
            _apply_event_linked_sales_payload(
                event_location,
                payload,
                source_import_id=locked_import.id,
                approval_batch_id=approval_batch_id,
            )
            _apply_event_linked_summary_delta(
                event_location.id,
                payload.get("summary") or {},
                mode="add",
            )

            for import_location in grouped_locations:
                import_location.approval_batch_id = approval_batch_id
                location_metadata = _parse_sales_import_location_metadata(
                    import_location
                )
                location_metadata["approval_batch_id"] = approval_batch_id
                location_metadata["approved_at"] = approval_time.isoformat()
                location_metadata["mode"] = "event_location"
                location_metadata["event_location_id"] = event_location_id
                location_metadata["event_sales_strategy"] = "append"
                location_metadata["applied_summary"] = (
                    _build_event_linked_sales_summary_contribution(
                        [import_location]
                    )
                )
                location_metadata.pop("previous_state", None)
                _write_sales_import_location_metadata(
                    import_location, location_metadata
                )

                for row in import_location.rows:
                    row_review = row_review_data.get(row.id, {})
                    if row.product_id is None or not row_review.get("is_active"):
                        continue
                    row.approval_batch_id = approval_batch_id
                    metadata = _parse_sales_import_row_metadata(row)
                    metadata["approval_batch_id"] = approval_batch_id
                    metadata["approved_at"] = approval_time.isoformat()
                    metadata["target"] = {
                        "mode": "event_location",
                        "event_location_id": event_location_id,
                    }
                    _write_sales_import_row_metadata(row, metadata)
                    row_change_count += 1

        for import_location in locked_import.locations:
            if import_location.event_location_id is not None:
                continue
            active_rows = [
                row
                for row in import_location.rows
                if row_review_data.get(row.id, {}).get("is_active")
            ]
            if import_location.location_id is None or not active_rows:
                continue
            import_location.approval_batch_id = approval_batch_id
            for row in import_location.rows:
                row_review = row_review_data.get(row.id, {})
                if row.product_id is None or not row_review.get("is_active"):
                    continue

                product = row.product
                if product is None:
                    continue

                row_changes: list[dict] = []
                for recipe_item in product.recipe_items:
                    if recipe_item.item_id is None:
                        continue
                    record = LocationStandItem.query.filter_by(
                        location_id=import_location.location_id,
                        item_id=recipe_item.item_id,
                    ).first()
                    is_countable = (
                        record.countable if record is not None else recipe_item.countable
                    )
                    if not is_countable:
                        continue
                    units_per_product = recipe_item_base_units_per_sale(recipe_item)
                    if units_per_product <= 0:
                        continue

                    sold_quantity = float(row.quantity or 0.0)
                    delta = sold_quantity * units_per_product
                    if abs(delta) < 1e-9:
                        continue

                    if record is None:
                        record = LocationStandItem(
                            location_id=import_location.location_id,
                            item_id=recipe_item.item_id,
                            countable=True,
                            expected_count=0,
                            purchase_gl_code_id=(
                                recipe_item.item.purchase_gl_code_id
                                if recipe_item.item is not None
                                else None
                            ),
                        )
                        db.session.add(record)
                        db.session.flush()
                    elif (
                        record.purchase_gl_code_id is None
                        and recipe_item.item is not None
                        and recipe_item.item.purchase_gl_code_id is not None
                    ):
                        record.purchase_gl_code_id = recipe_item.item.purchase_gl_code_id
                    record.countable = True

                    expected_before = float(record.expected_count or 0.0)
                    expected_after = expected_before - delta
                    record.expected_count = expected_after

                    item = db.session.get(Item, recipe_item.item_id)
                    item_qty_before = float(item.quantity or 0.0) if item else 0.0
                    item_qty_after = item_qty_before - delta
                    if item is not None:
                        item.quantity = item_qty_after

                    row_changes.append(
                        {
                            "item_id": recipe_item.item_id,
                            "location_id": import_location.location_id,
                            "location_stand_item_id": record.id,
                            "expected_count_before": expected_before,
                            "expected_count_after": expected_after,
                            "item_quantity_before": item_qty_before,
                            "item_quantity_after": item_qty_after,
                            "consumed_quantity": delta,
                        }
                    )

                row.approval_batch_id = approval_batch_id
                metadata = _parse_sales_import_row_metadata(row)
                if row_changes:
                    metadata["approval_batch_id"] = approval_batch_id
                    metadata["approved_at"] = approval_time.isoformat()
                    metadata["changes"] = row_changes
                    _write_sales_import_row_metadata(row, metadata)
                    row_change_count += 1

        locked_import.status = "approved"
        locked_import.approved_by = current_user.id
        locked_import.approved_at = approval_time
        locked_import.approval_batch_id = approval_batch_id
        db.session.commit()
        success_message = "Import approved."
        if row_change_count:
            success_message += (
                f" Applied mapped sales for {row_change_count} row"
                f"{'s' if row_change_count != 1 else ''}."
            )
        flash(success_message, "success")
        log_activity(
            f"Approved POS sales import {locked_import.id} "
            f"(batch {approval_batch_id})"
        )
        return True
    except Exception:
        db.session.rollback()
        flash("Unable to approve import due to an unexpected error.", "danger")
        return False


def _check_negative_sales_import_reverse(import_record: PosSalesImport) -> list[str]:
    """Return warnings if reversing an approved import could cause negative inventory."""

    warnings: list[str] = []
    for row in import_record.rows:
        for change in _parse_sales_import_approval_changes(row):
            try:
                consumed_quantity = float(change.get("consumed_quantity") or 0.0)
            except (TypeError, ValueError):
                continue
            if abs(consumed_quantity) < 1e-9:
                continue

            item_id = change.get("item_id")
            location_id = change.get("location_id")
            if item_id is None:
                continue

            item = db.session.get(Item, item_id)
            if item is None:
                warnings.append(
                    f"Cannot reverse import row '{row.source_product_name}' because linked item ID {item_id} no longer exists."
                )
                continue

            stand_record = None
            stand_record_id = change.get("location_stand_item_id")
            if stand_record_id is not None:
                stand_record = db.session.get(LocationStandItem, stand_record_id)
            if stand_record is None and location_id is not None:
                stand_record = LocationStandItem.query.filter_by(
                    location_id=location_id,
                    item_id=item_id,
                ).first()

            location_name = "Unknown location"
            if stand_record is not None and stand_record.location is not None:
                location_name = stand_record.location.name
            elif location_id is not None:
                mapped_location = db.session.get(Location, location_id)
                if mapped_location is not None:
                    location_name = mapped_location.name

            current_expected = (
                float(stand_record.expected_count or 0.0) if stand_record is not None else 0.0
            )
            expected_after_reverse = current_expected + consumed_quantity
            if expected_after_reverse < 0:
                warnings.append(
                    f"Reversing this import will result in negative inventory for {item.name} at {location_name}."
                )

            current_item_qty = float(item.quantity or 0.0)
            item_qty_after_reverse = current_item_qty + consumed_quantity
            if item_qty_after_reverse < 0:
                warnings.append(
                    f"Reversing this import will make global inventory negative for {item.name}."
                )
    return warnings


@admin.route("/controlpanel/sales-imports/<int:import_id>/download", methods=["GET"])
@login_required
def download_sales_import_attachment(import_id: int):
    """Download the original attachment for a staged POS sales import."""
    from flask import send_from_directory

    sales_import = PosSalesImport.query.filter(PosSalesImport.id == import_id).first_or_404()
    attachment_path = _get_sales_import_attachment_path(sales_import)
    if attachment_path is None:
        abort(404)

    log_activity(f"Downloaded POS sales import attachment for import {sales_import.id}")
    return send_from_directory(
        os.path.dirname(attachment_path),
        os.path.basename(attachment_path),
        as_attachment=True,
        download_name=sales_import.attachment_filename
        or os.path.basename(attachment_path),
    )


@admin.route("/controlpanel/sales-imports/<int:import_id>", methods=["GET", "POST"])
@login_required
def sales_import_detail(import_id: int):
    """Render location and row-level detail for a staged POS sales import."""
    is_ajax_request = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    ajax_review_response: dict[str, str] | None = None
    active_location_filter = _normalize_sales_import_location_filter(
        request.values.get("location_filter")
    )
    sales_import = (
        PosSalesImport.query.options(
            selectinload(PosSalesImport.locations)
            .selectinload(PosSalesImportLocation.rows)
            .selectinload(PosSalesImportRow.product),
            selectinload(PosSalesImport.locations).selectinload(PosSalesImportLocation.location),
            selectinload(PosSalesImport.locations)
            .selectinload(PosSalesImportLocation.event_location)
            .selectinload(EventLocation.event),
            selectinload(PosSalesImport.locations)
            .selectinload(PosSalesImportLocation.event_location)
            .selectinload(EventLocation.location),
            selectinload(PosSalesImport.approver),
            selectinload(PosSalesImport.reverser),
            selectinload(PosSalesImport.deleter),
        )
        .filter(PosSalesImport.id == import_id)
        .first_or_404()
    )
    attachment_available = _get_sales_import_attachment_path(sales_import) is not None

    def _apply_auto_mappings() -> bool:
        changed = False

        exact_location_lookup = {
            (location.name or "").strip().casefold(): location.id
            for location in Location.query.all()
            if location.name
        }
        exact_product_lookup = {
            (product.name or "").strip().casefold(): product.id
            for product in Product.query.all()
            if product.name
        }

        location_alias_lookup = {
            alias.normalized_name: alias.location_id
            for alias in TerminalSaleLocationAlias.query.all()
            if alias.normalized_name and alias.location_id
        }
        product_alias_lookup = {
            alias.normalized_name: alias.product_id
            for alias in TerminalSaleProductAlias.query.all()
            if alias.normalized_name and alias.product_id
        }

        normalized_location_lookup = {
            normalize_pos_alias(location.name or ""): location.id
            for location in Location.query.all()
            if location.name
        }
        normalized_product_lookup = {
            normalize_pos_alias(product.name or ""): product.id
            for product in Product.query.all()
            if product.name
        }

        for location in sales_import.locations:
            if location.location_id is None:
                exact_key = (location.source_location_name or "").strip().casefold()
                normalized_key = location.normalized_location_name or normalize_pos_alias(
                    location.source_location_name or ""
                )
                matched_location_id = exact_location_lookup.get(exact_key)
                if matched_location_id is None and normalized_key:
                    matched_location_id = location_alias_lookup.get(normalized_key)
                if matched_location_id is None and normalized_key:
                    matched_location_id = normalized_location_lookup.get(normalized_key)
                if matched_location_id is not None:
                    location.location_id = matched_location_id
                    changed = True

            for row in location.rows:
                if row.product_id is not None:
                    continue
                exact_key = (row.source_product_name or "").strip().casefold()
                normalized_key = row.normalized_product_name or normalize_pos_alias(
                    row.source_product_name or ""
                )
                matched_product_id = exact_product_lookup.get(exact_key)
                if matched_product_id is None and normalized_key:
                    matched_product_id = product_alias_lookup.get(normalized_key)
                if matched_product_id is None and normalized_key:
                    matched_product_id = normalized_product_lookup.get(normalized_key)
                if matched_product_id is not None:
                    row.product_id = matched_product_id
                    changed = True

        return changed

    def _sync_detail_review_state() -> dict[str, Any]:
        assignment_changed = _sync_sales_import_event_assignments(sales_import)
        issue_state = _refresh_sales_import_mapping_status(sales_import)
        issue_state["assignment_changed"] = assignment_changed
        return issue_state

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        selected_location_id = request.form.get("selected_location_id", type=int)

        def _redirect_review_locked():
            locked_message = _sales_import_review_locked_message(sales_import)
            if is_ajax_request and action == "resolve_row_price":
                return (
                    jsonify(
                        success=False,
                        message=locked_message,
                        category="warning",
                    ),
                    409,
                )
            flash(locked_message, "warning")
            return redirect(
                url_for(
                    "admin.sales_import_detail",
                    import_id=sales_import.id,
                    location_id=selected_location_id,
                    location_filter=active_location_filter,
                )
            )

        if action == "save_sales_date":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            raw_sales_date = (request.form.get("sales_date") or "").strip()
            if not raw_sales_date:
                flash("Select the sales date before saving.", "warning")
            else:
                try:
                    sales_import.sales_date = date_cls.fromisoformat(raw_sales_date)
                except ValueError:
                    flash("Enter a valid sales date before saving.", "warning")
                else:
                    _sync_detail_review_state()
                    db.session.commit()
                    flash("Sales date saved.", "success")
                    log_activity(
                        f"Saved sales date for POS sales import {sales_import.id}: "
                        f"{sales_import.sales_date.isoformat()}"
                    )

        elif action == "map_location":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            location_import_id = request.form.get("location_import_id", type=int)
            target_location_id = request.form.get("target_location_id", type=int)
            location_record = next(
                (loc for loc in sales_import.locations if loc.id == location_import_id),
                None,
            )
            if not location_record:
                flash("Unable to find the selected import location.", "danger")
            elif not target_location_id:
                flash("Select a location to map.", "warning")
            else:
                normalized_key = location_record.normalized_location_name
                for scoped_location in sales_import.locations:
                    if scoped_location.normalized_location_name == normalized_key:
                        scoped_location.location_id = target_location_id

                alias = TerminalSaleLocationAlias.query.filter_by(
                    normalized_name=normalized_key
                ).first()
                if alias is None:
                    alias = TerminalSaleLocationAlias(
                        source_name=location_record.source_location_name,
                        normalized_name=normalized_key,
                        location_id=target_location_id,
                    )
                    db.session.add(alias)
                else:
                    alias.source_name = location_record.source_location_name
                    alias.location_id = target_location_id
                _sync_detail_review_state()
                db.session.commit()
                flash("Location mapping saved.", "success")
                log_activity(
                    f"Saved location mapping for POS sales import {sales_import.id}: "
                    f"'{location_record.source_location_name}' -> location {target_location_id}"
                )

        elif action == "create_location":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            if not current_user.has_permission("locations.create"):
                abort(403)
            location_import_id = request.form.get("location_import_id", type=int)
            new_location_name = (request.form.get("new_location_name") or "").strip()
            location_record = next(
                (loc for loc in sales_import.locations if loc.id == location_import_id),
                None,
            )
            if not location_record:
                flash("Unable to find the selected import location.", "danger")
            elif not new_location_name:
                flash("Enter a new location name before creating.", "warning")
            else:
                existing = Location.query.filter_by(name=new_location_name).first()
                if existing:
                    created_location = existing
                else:
                    created_location = Location(name=new_location_name)
                    db.session.add(created_location)
                    db.session.flush()

                normalized_key = location_record.normalized_location_name
                for scoped_location in sales_import.locations:
                    if scoped_location.normalized_location_name == normalized_key:
                        scoped_location.location_id = created_location.id

                alias = TerminalSaleLocationAlias.query.filter_by(
                    normalized_name=normalized_key
                ).first()
                if alias is None:
                    alias = TerminalSaleLocationAlias(
                        source_name=location_record.source_location_name,
                        normalized_name=normalized_key,
                        location_id=created_location.id,
                    )
                    db.session.add(alias)
                else:
                    alias.source_name = location_record.source_location_name
                    alias.location_id = created_location.id
                _sync_detail_review_state()
                db.session.commit()
                flash("Location created and mapping saved.", "success")
                log_activity(
                    f"Created/saved location mapping for POS sales import {sales_import.id}: "
                    f"'{location_record.source_location_name}' -> location {created_location.id}"
                )

        elif action == "toggle_location_skip":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            location_import_id = request.form.get("location_import_id", type=int)
            should_skip = request.form.get("skip_location") == "1"
            location_record = next(
                (loc for loc in sales_import.locations if loc.id == location_import_id),
                None,
            )
            if not location_record:
                flash("Unable to find the selected import location.", "danger")
            else:
                payload = _parse_sales_import_location_metadata(location_record)
                if should_skip:
                    payload["review"] = {
                        "skip": True,
                        "updated_at": datetime.utcnow().isoformat(),
                        "updated_by": current_user.id,
                    }
                    flash(
                        "Location skipped. Approval will ignore this location and its rows.",
                        "success",
                    )
                    log_activity(
                        f"Skipped import location {location_record.id} on POS sales import {sales_import.id}"
                    )
                else:
                    payload.pop("review", None)
                    flash(
                        "Location included again. Resolve any remaining issues before approval.",
                        "success",
                    )
                    log_activity(
                        f"Included import location {location_record.id} again on POS sales import {sales_import.id}"
                    )
                _write_sales_import_location_metadata(location_record, payload)
                _sync_detail_review_state()
                db.session.commit()

        elif action == "map_product":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            row_id = request.form.get("row_id", type=int)
            target_product_id = request.form.get("target_product_id", type=int)
            row_record = next(
                (
                    row
                    for location in sales_import.locations
                    for row in location.rows
                    if row.id == row_id
                ),
                None,
            )
            if not row_record:
                flash("Unable to find the selected import row.", "danger")
            elif not target_product_id:
                flash("Select a product to map.", "warning")
            else:
                normalized_key = row_record.normalized_product_name
                for scoped_row in sales_import.rows:
                    if scoped_row.normalized_product_name == normalized_key:
                        scoped_row.product_id = target_product_id

                alias = TerminalSaleProductAlias.query.filter_by(
                    normalized_name=normalized_key
                ).first()
                if alias is None:
                    alias = TerminalSaleProductAlias(
                        source_name=row_record.source_product_name,
                        normalized_name=normalized_key,
                        product_id=target_product_id,
                    )
                    db.session.add(alias)
                else:
                    alias.source_name = row_record.source_product_name
                    alias.product_id = target_product_id
                _sync_detail_review_state()
                db.session.commit()
                flash("Product mapping saved.", "success")
                log_activity(
                    f"Saved product mapping for POS sales import {sales_import.id}: "
                    f"'{row_record.source_product_name}' -> product {target_product_id}"
                )

        elif action == "create_product":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            row_id = request.form.get("row_id", type=int)
            row_record = next(
                (
                    row
                    for location in sales_import.locations
                    for row in location.rows
                    if row.id == row_id
                ),
                None,
            )
            if not row_record:
                flash("Unable to find the selected import row.", "danger")
            else:
                target_location_id = selected_location_id or row_record.location_import_id
                flash(
                    "Complete the full product form, then the new product will map back to this sales import row.",
                    "info",
                )
                return redirect(
                    url_for(
                        "product.create_product",
                        sales_import_id=sales_import.id,
                        import_row_id=row_record.id,
                        return_location_id=target_location_id,
                        location_filter=active_location_filter,
                    )
                )

        elif action == "resolve_row_price":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            row_id = request.form.get("row_id", type=int)
            resolution = _normalize_sales_import_price_action(
                request.form.get("price_resolution")
            )
            row_record = next(
                (
                    row
                    for location in sales_import.locations
                    for row in location.rows
                    if row.id == row_id
                ),
                None,
            )
            if not row_record:
                if is_ajax_request:
                    return (
                        jsonify(
                            success=False,
                            message="Unable to find the selected import row.",
                            category="danger",
                        ),
                        404,
                    )
                flash("Unable to find the selected import row.", "danger")
            elif resolution is None:
                if is_ajax_request:
                    return (
                        jsonify(
                            success=False,
                            message="Choose how this row should handle pricing before saving.",
                            category="warning",
                        ),
                        400,
                    )
                flash(
                    "Choose how this row should handle pricing before saving.",
                    "warning",
                )
            elif row_record.product_id is None and resolution != "skip":
                if is_ajax_request:
                    return (
                        jsonify(
                            success=False,
                            message="Map the product before choosing a row price.",
                            category="warning",
                        ),
                        400,
                    )
                flash("Map the product before choosing a row price.", "warning")
            else:
                selected_price = None
                if resolution == "custom":
                    selected_price = coerce_float(request.form.get("custom_price"))
                    if selected_price is None:
                        if is_ajax_request:
                            return (
                                jsonify(
                                    success=False,
                                    message="Enter a valid custom price before saving.",
                                    category="warning",
                                ),
                                400,
                            )
                        flash("Enter a valid custom price before saving.", "warning")
                        return redirect(
                            url_for(
                                "admin.sales_import_detail",
                                import_id=sales_import.id,
                                location_id=selected_location_id,
                                location_filter=active_location_filter,
                            )
                        )

                payload = _parse_sales_import_row_metadata(row_record)
                review = _get_sales_import_row_review(row_record).copy()
                review["price_action"] = resolution
                review["selected_price"] = (
                    selected_price if resolution == "custom" else None
                )
                review["updated_at"] = datetime.utcnow().isoformat()
                review["updated_by"] = current_user.id
                payload["review"] = review
                _write_sales_import_row_metadata(row_record, payload)
                _sync_detail_review_state()
                db.session.commit()

                if resolution == "skip":
                    message = (
                        "Row skipped. It will be excluded from stock operations and price updates."
                    )
                elif resolution == "file":
                    message = "This row will use the file price on approval."
                elif resolution == "app":
                    message = "This row will keep the app price on approval."
                else:
                    message = "Custom row price saved."

                if is_ajax_request:
                    ajax_review_response = {
                        "message": message,
                        "category": "success",
                    }
                else:
                    flash(message, "success")

                log_activity(
                    f"Saved price review for POS sales import {sales_import.id} row {row_record.id}: "
                    f"{resolution}"
                )

        elif action == "refresh_auto_mapping":
            if not _sales_import_review_is_editable(sales_import):
                return _redirect_review_locked()
            auto_mapping_changed = _apply_auto_mappings()
            issue_state = _sync_detail_review_state()
            if (
                auto_mapping_changed
                or issue_state["assignment_changed"]
                or issue_state["status_changed"]
            ):
                db.session.commit()
                flash("Applied latest automatic mappings.", "success")
                log_activity(f"Refreshed automatic mappings for POS sales import {sales_import.id}")
            else:
                flash("No additional automatic mappings were found.", "info")
        elif action == "approve_import":
            _approve_sales_import(sales_import.id)
        elif action == "undo_approved_import":
            reversal_reason = (request.form.get("reversal_reason") or "").strip()
            has_warning_confirmation = request.form.get("confirm_reversal") == "1"

            if not reversal_reason:
                flash("Enter a reversal reason before undoing an approved import.", "warning")
                return redirect(
                    url_for(
                        "admin.sales_import_detail",
                        import_id=sales_import.id,
                        location_id=selected_location_id,
                        location_filter=active_location_filter,
                    )
                )

            try:
                locked_import = (
                    PosSalesImport.query.filter(PosSalesImport.id == sales_import.id)
                    .with_for_update()
                    .first()
                )
                if locked_import is None:
                    flash("The requested import could not be found.", "danger")
                    return redirect(url_for("admin.sales_imports"))

                locked_import = (
                    PosSalesImport.query.options(
                        selectinload(PosSalesImport.locations).selectinload(
                            PosSalesImportLocation.rows
                        ),
                        selectinload(PosSalesImport.locations).selectinload(
                            PosSalesImportLocation.event_location
                        ).selectinload(EventLocation.event),
                        selectinload(PosSalesImport.locations).selectinload(
                            PosSalesImportLocation.event_location
                        ).selectinload(EventLocation.terminal_sales),
                        selectinload(PosSalesImport.locations).selectinload(
                            PosSalesImportLocation.event_location
                        ).selectinload(EventLocation.terminal_sales_summary),
                        selectinload(PosSalesImport.rows),
                    )
                    .filter(PosSalesImport.id == sales_import.id)
                    .first()
                )
                if locked_import is None:
                    flash("The requested import could not be found.", "danger")
                    return redirect(url_for("admin.sales_imports"))

                if locked_import.status != "approved":
                    flash(
                        "Undo is only allowed when the import status is Approved.",
                        "warning",
                    )
                    return redirect(
                        url_for(
                            "admin.sales_import_detail",
                            import_id=sales_import.id,
                            location_id=selected_location_id,
                            location_filter=active_location_filter,
                        )
                    )

                warnings = _check_negative_sales_import_reverse(locked_import)
                if warnings and not has_warning_confirmation:
                    flash(
                        "Undo blocked: this reversal may cause negative inventory. Confirm to continue.",
                        "warning",
                    )
                    for warning in warnings:
                        flash(warning, "warning")
                    return redirect(
                        url_for(
                            "admin.sales_import_detail",
                            import_id=sales_import.id,
                            location_id=selected_location_id,
                            location_filter=active_location_filter,
                        )
                    )

                reversal_time = datetime.utcnow()
                reversal_batch_id = f"pos-import-reverse-{locked_import.id}-{uuid.uuid4().hex[:12]}"
                row_change_count = 0
                restored_event_location_ids: set[int] = set()
                reversed_event_sales_batches: set[tuple[int, str | None]] = set()
                event_linked_locations_by_event: dict[int, list[PosSalesImportLocation]] = {}
                for import_location in locked_import.locations:
                    event_location_id = (
                        import_location.event_location_id
                        or _parse_sales_import_location_metadata(import_location).get(
                            "event_location_id"
                        )
                    )
                    if event_location_id is None:
                        continue
                    event_linked_locations_by_event.setdefault(
                        event_location_id, []
                    ).append(import_location)

                for import_location in locked_import.locations:
                    location_metadata = _parse_sales_import_location_metadata(
                        import_location
                    )
                    if location_metadata.get("mode") == "event_location":
                        import_location.reversal_batch_id = reversal_batch_id
                        event_location_id = (
                            location_metadata.get("event_location_id")
                            or import_location.event_location_id
                        )
                        applied_batch_id = (
                            import_location.approval_batch_id
                            or location_metadata.get("approval_batch_id")
                        )
                        event_sales_strategy = (
                            location_metadata.get("event_sales_strategy")
                            or "replace_snapshot"
                        )
                        skipped_closed_event_restore = False
                        if (
                            event_sales_strategy == "append"
                            and event_location_id is not None
                        ):
                            batch_key = (event_location_id, applied_batch_id)
                            if batch_key not in reversed_event_sales_batches:
                                delete_query = TerminalSale.query.filter_by(
                                    event_location_id=event_location_id
                                )
                                if applied_batch_id:
                                    delete_query = delete_query.filter_by(
                                        approval_batch_id=applied_batch_id
                                    )
                                else:
                                    delete_query = delete_query.filter_by(
                                        pos_sales_import_id=locked_import.id
                                    )
                                delete_query.delete(synchronize_session=False)
                                _apply_event_linked_summary_delta(
                                    event_location_id,
                                    _build_event_linked_sales_summary_contribution(
                                        event_linked_locations_by_event.get(
                                            event_location_id, [import_location]
                                        )
                                    ),
                                    mode="subtract",
                                    excluded_import_id=locked_import.id,
                                )
                                reversed_event_sales_batches.add(batch_key)
                        elif event_location_id and event_location_id not in restored_event_location_ids:
                            event_location = import_location.event_location
                            if (
                                event_location is not None
                                and event_location.event is not None
                                and event_location.event.closed
                            ):
                                skipped_closed_event_restore = True
                            else:
                                _restore_event_location_sales_state(
                                    event_location_id,
                                    location_metadata.get("previous_state"),
                                )
                            restored_event_location_ids.add(event_location_id)

                        location_metadata["reversal"] = {
                            "reversal_batch_id": reversal_batch_id,
                            "reversed_at": reversal_time.isoformat(),
                            "reversed_by": current_user.id,
                            "reason": reversal_reason,
                            "mode": "event_location",
                            "event_location_id": event_location_id,
                            "event_sales_strategy": event_sales_strategy,
                            "approval_batch_id": applied_batch_id,
                            "skipped_closed_event_restore": skipped_closed_event_restore,
                        }
                        _write_sales_import_location_metadata(
                            import_location, location_metadata
                        )

                        for row in import_location.rows:
                            if not row.approval_batch_id:
                                continue
                            row.reversal_batch_id = reversal_batch_id
                            metadata = _parse_sales_import_row_metadata(row)
                            metadata["reversal"] = {
                                "reversal_batch_id": reversal_batch_id,
                                "reversed_at": reversal_time.isoformat(),
                                "reversed_by": current_user.id,
                                "reason": reversal_reason,
                                "mode": "event_location",
                                "event_location_id": event_location_id,
                                "event_sales_strategy": event_sales_strategy,
                                "approval_batch_id": applied_batch_id,
                                "skipped_closed_event_restore": skipped_closed_event_restore,
                            }
                            row.approval_metadata = json.dumps(metadata)
                            row_change_count += 1
                        continue

                    if import_location.approval_batch_id:
                        import_location.reversal_batch_id = reversal_batch_id
                    for row in import_location.rows:
                        row_changes = _parse_sales_import_approval_changes(row)
                        if not row_changes:
                            continue

                        reversal_changes: list[dict] = []
                        for change in row_changes:
                            item_id = change.get("item_id")
                            location_id = change.get("location_id")
                            if item_id is None:
                                continue

                            try:
                                consumed_quantity = float(
                                    change.get("consumed_quantity") or 0.0
                                )
                            except (TypeError, ValueError):
                                continue
                            if abs(consumed_quantity) < 1e-9:
                                continue

                            stand_record = None
                            stand_record_id = change.get("location_stand_item_id")
                            if stand_record_id is not None:
                                stand_record = db.session.get(
                                    LocationStandItem, stand_record_id
                                )
                            if stand_record is None and location_id is not None:
                                stand_record = LocationStandItem.query.filter_by(
                                    location_id=location_id,
                                    item_id=item_id,
                                ).first()
                            if stand_record is None and location_id is not None:
                                stand_record = LocationStandItem(
                                    location_id=location_id,
                                    item_id=item_id,
                                    countable=True,
                                    expected_count=0,
                                )
                                db.session.add(stand_record)
                                db.session.flush()

                            expected_before = (
                                float(stand_record.expected_count or 0.0)
                                if stand_record is not None
                                else 0.0
                            )
                            expected_after = expected_before + consumed_quantity
                            if stand_record is not None:
                                stand_record.countable = True
                                stand_record.expected_count = expected_after

                            item = db.session.get(Item, item_id)
                            item_qty_before = float(item.quantity or 0.0) if item else 0.0
                            item_qty_after = item_qty_before + consumed_quantity
                            if item is not None:
                                item.quantity = item_qty_after

                            reversal_changes.append(
                                {
                                    "item_id": item_id,
                                    "location_id": location_id,
                                    "location_stand_item_id": (
                                        stand_record.id if stand_record is not None else None
                                    ),
                                    "expected_count_before": expected_before,
                                    "expected_count_after": expected_after,
                                    "item_quantity_before": item_qty_before,
                                    "item_quantity_after": item_qty_after,
                                    "reversed_quantity": consumed_quantity,
                                }
                            )

                        row.reversal_batch_id = reversal_batch_id
                        if reversal_changes:
                            metadata = {}
                            if row.approval_metadata:
                                try:
                                    metadata = json.loads(row.approval_metadata)
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    metadata = {}
                            metadata["reversal"] = {
                                "reversal_batch_id": reversal_batch_id,
                                "reversed_at": reversal_time.isoformat(),
                                "reversed_by": current_user.id,
                                "reason": reversal_reason,
                                "changes": reversal_changes,
                            }
                            row.approval_metadata = json.dumps(metadata)
                            row_change_count += 1

                locked_import.status = PosSalesImport.STATUS_PENDING
                locked_import.reversed_by = current_user.id
                locked_import.reversed_at = reversal_time
                locked_import.reversal_batch_id = reversal_batch_id
                locked_import.reversal_reason = reversal_reason
                db.session.commit()
                success_message = (
                    "Import reversal complete. The import is back in Pending review and can be approved again."
                )
                if row_change_count:
                    success_message += (
                        f" Reversed approved sales rows for {row_change_count} row"
                        f"{'s' if row_change_count != 1 else ''}."
                    )
                flash(success_message, "success")
                log_activity(
                    f"Reversed POS sales import {locked_import.id} "
                    f"(batch {reversal_batch_id}) with reason: {reversal_reason}"
                )
            except Exception:
                db.session.rollback()
                flash("Unable to undo approved import due to an unexpected error.", "danger")
        elif action == "delete_import":
            deletion_reason = (request.form.get("deletion_reason") or "").strip()
            try:
                locked_import = (
                    PosSalesImport.query.filter(PosSalesImport.id == sales_import.id)
                    .with_for_update()
                    .first()
                )
                if locked_import is None:
                    flash("The requested import could not be found.", "danger")
                    return redirect(url_for("admin.sales_imports"))

                if locked_import.status == "approved":
                    flash(
                        "Approved imports must be undone before they can be deleted.",
                        "warning",
                    )
                    return redirect(
                        url_for(
                            "admin.sales_import_detail",
                            import_id=sales_import.id,
                            location_id=selected_location_id,
                            location_filter=active_location_filter,
                        )
                    )

                if locked_import.status == "deleted":
                    flash("This import is already deleted.", "info")
                    return redirect(
                        url_for(
                            "admin.sales_import_detail",
                            import_id=sales_import.id,
                            location_id=selected_location_id,
                            location_filter=active_location_filter,
                        )
                    )

                locked_import.status = "deleted"
                locked_import.deleted_by = current_user.id
                locked_import.deleted_at = datetime.utcnow()
                locked_import.deletion_reason = deletion_reason or None
                _detach_sales_import_attachment(locked_import)
                db.session.commit()
                flash("Import marked as deleted.", "success")
                log_activity(
                    f"Soft-deleted POS sales import {locked_import.id}"
                    + (
                        f" with reason: {deletion_reason}"
                        if deletion_reason
                        else ""
                    )
                )
                return redirect(url_for("admin.sales_imports"))
            except Exception:
                db.session.rollback()
                flash("Unable to delete import due to an unexpected error.", "danger")

        return redirect(
            url_for(
                "admin.sales_import_detail",
                import_id=sales_import.id,
                location_id=selected_location_id,
                location_filter=active_location_filter,
            )
        )

    issue_state = _sync_detail_review_state()
    if issue_state["assignment_changed"] or issue_state["status_changed"]:
        db.session.commit()
    unresolved_location_count = issue_state["unresolved_location_count"]
    unresolved_event_location_count = issue_state["unresolved_event_location_count"]
    unresolved_row_count = issue_state["unresolved_row_count"]
    needs_mapping = issue_state["needs_mapping"]

    review_context = issue_state["review_context"]
    location_review_data = review_context["location_review_data"]
    row_review_data = review_context["row_review_data"]
    location_discount_totals = review_context["location_discount_totals"]
    unresolved_price_count = issue_state["unresolved_price_count"]
    unresolved_location_ids = review_context["unresolved_location_ids"]
    unresolved_row_ids = review_context["unresolved_row_ids"]
    unresolved_price_row_ids = review_context["unresolved_price_row_ids"]
    event_assignment_state = issue_state["event_assignment_state"]
    unresolved_event_location_ids = event_assignment_state[
        "unresolved_event_location_ids"
    ]
    conflicting_event_location_ids = event_assignment_state[
        "conflicting_event_location_ids"
    ]
    direct_inventory_only_location_ids = event_assignment_state[
        "direct_inventory_only_location_ids"
    ]
    event_assignment_messages = event_assignment_state["event_assignment_messages"]
    candidate_event_locations_by_import_location = event_assignment_state[
        "candidate_event_locations_by_import_location"
    ]
    event_assignment_labels = {
        location.id: _format_sales_import_event_label(location.event_location)
        for location in sales_import.locations
        if location.event_location is not None
    }

    location_issue_counts: dict[int, int] = {}
    for location in sales_import.locations:
        location_issue_counts[location.id] = int(location.id in unresolved_location_ids)
        location_issue_counts[location.id] += int(
            location.id in unresolved_event_location_ids
        )
        location_issue_counts[location.id] += sum(
            1 for row in location.rows if row.id in unresolved_row_ids
        )
        location_issue_counts[location.id] += sum(
            1 for row in location.rows if row.id in unresolved_price_row_ids
        )

    sorted_locations = sorted(
        sales_import.locations,
        key=lambda location: (
            3
            if location_review_data.get(location.id, {}).get("is_skipped")
            else 0
            if location.location_id is None
            else 1
            if location_issue_counts.get(location.id, 0) > 0
            else 2,
            (location.source_location_name or "").casefold(),
            location.parse_index or 0,
            location.id,
        ),
    )
    all_location_count = len(sorted_locations)
    filtered_locations = [
        location
        for location in sorted_locations
        if active_location_filter != "issues"
        or location_issue_counts.get(location.id, 0) > 0
    ]

    selected_location_id = request.values.get("selected_location_id", type=int)
    if selected_location_id is None:
        selected_location_id = request.values.get("location_id", type=int)
    selected_location = None
    if selected_location_id is not None:
        selected_location = next(
            (
                location
                for location in filtered_locations
                if location.id == selected_location_id
            ),
            None,
        )
    if selected_location is None and filtered_locations:
        selected_location = filtered_locations[0]

    import_totals = {
        "quantity": sum(float(loc.total_quantity or 0.0) for loc in sales_import.locations),
        "net_inc": sum(float(loc.net_inc or 0.0) for loc in sales_import.locations),
        "discount": review_context["import_discount_total"],
        "computed_total": sum(
            float(loc.computed_total or 0.0) for loc in sales_import.locations
        ),
    }

    location_errors: dict[int, list[str]] = {}
    row_errors: dict[int, list[str]] = {}
    for location in sales_import.locations:
        errors: list[str] = []
        location_review = location_review_data.get(location.id, {})
        if location_review.get("is_skipped"):
            errors.append(
                "Location is skipped. Approval will ignore its rows, mapping requirements, and event routing."
            )
        elif location.id in review_context["unresolved_location_ids"]:
            errors.append("Location is not mapped.")
        if location.id in unresolved_event_location_ids:
            errors.extend(event_assignment_messages.get(location.id, []))
        location_errors[location.id] = errors

        for row in location.rows:
            row_validation_errors: list[str] = []
            row_review = row_review_data.get(row.id, {})
            if row_review.get("location_is_skipped"):
                row_validation_errors.append(
                    "Location is skipped; this row will not affect inventory, event sales, or product pricing."
                )
            elif row_review.get("is_skipped"):
                row_validation_errors.append(
                    "Row is skipped; it will not affect inventory or update product pricing."
                )
            elif row_review.get("requires_mapping"):
                row_validation_errors.append("Product is not mapped.")
            if not row_review.get("location_is_skipped") and row.is_zero_quantity:
                row_validation_errors.append(
                    "Quantity is zero; treat as informational and exclude from stock operations."
                )
            if row_review.get("requires_price_resolution"):
                row_validation_errors.append(
                    "Resolve the file/app price difference before approval."
                )
            if row_review.get("has_price_conflict"):
                row_validation_errors.append(
                    "Chosen price conflicts with another row for this product in this import."
                )
            row_errors[row.id] = row_validation_errors

    reversal_warnings: list[str] = []
    if sales_import.status == "approved":
        reversal_warnings = _check_negative_sales_import_reverse(sales_import)
    undo_confirm_form = ConfirmForm()
    available_products = []
    if current_user.has_permission("sales_imports.manage"):
        available_products = Product.query.order_by(Product.name).all()

    detail_context = dict(
        sales_import=sales_import,
        attachment_available=attachment_available,
        sorted_locations=filtered_locations,
        all_location_count=all_location_count,
        visible_location_count=len(filtered_locations),
        active_location_filter=active_location_filter,
        location_issue_counts=location_issue_counts,
        location_review_data=location_review_data,
        selected_location=selected_location,
        import_totals=import_totals,
        location_errors=location_errors,
        row_errors=row_errors,
        locations=Location.query.order_by(Location.name).all(),
        products=available_products,
        candidate_event_locations_by_import_location=candidate_event_locations_by_import_location,
        conflicting_event_location_ids=conflicting_event_location_ids,
        direct_inventory_only_location_ids=direct_inventory_only_location_ids,
        event_assignment_labels=event_assignment_labels,
        event_assignment_messages=event_assignment_messages,
        needs_mapping=needs_mapping,
        unresolved_location_count=unresolved_location_count,
        unresolved_event_location_count=unresolved_event_location_count,
        unresolved_row_count=unresolved_row_count,
        unresolved_price_count=unresolved_price_count,
        reversal_warnings=reversal_warnings,
        row_review_data=row_review_data,
        location_discount_totals=location_discount_totals,
        can_approve_import=_sales_import_can_be_approved(sales_import),
        review_edit_locked=not _sales_import_review_is_editable(sales_import),
        price_review_locked=not _sales_import_review_is_editable(sales_import),
        undo_confirm_form=undo_confirm_form,
    )

    if ajax_review_response is not None:
        return jsonify(
            success=True,
            message=ajax_review_response["message"],
            category=ajax_review_response["category"],
            review_html=render_template(
                "admin/sales_import_detail.html", **detail_context
            ),
        )

    return render_template("admin/sales_import_detail.html", **detail_context)


@admin.route("/controlpanel/vendor-item-aliases", methods=["GET", "POST"])
@admin.route(
    "/controlpanel/vendor-item-aliases/<int:alias_id>/edit", methods=["GET", "POST"]
)
@login_required
def vendor_item_aliases(alias_id: int | None = None):
    """Allow admins to manage vendor item alias mappings."""

    def _safe_local_return_url(value: str | None) -> str | None:
        candidate = (value or "").strip().replace("\\", "")
        if not candidate:
            return None
        parsed = urlparse(candidate)
        if parsed.scheme or parsed.netloc or not candidate.startswith("/"):
            return None
        return candidate

    alias_obj = db.session.get(VendorItemAlias, alias_id) if alias_id else None
    form = VendorItemAliasForm(obj=alias_obj)
    delete_form = DeleteForm()
    filter_vendor_id = request.args.get("filter_vendor_id", type=int)
    filter_item_id = request.args.get("filter_item_id", type=int)
    filter_query = (request.args.get("filter_query") or "").strip()
    list_filter_args: dict[str, object] = {}
    if filter_vendor_id:
        list_filter_args["filter_vendor_id"] = filter_vendor_id
    if filter_item_id:
        list_filter_args["filter_item_id"] = filter_item_id
    if filter_query:
        list_filter_args["filter_query"] = filter_query
    list_return_url = url_for("admin.vendor_item_aliases", **list_filter_args)
    if request.method == "GET":
        form.return_to.data = (
            _safe_local_return_url(request.args.get("next")) or list_return_url
        )
        if alias_obj is None:
            requested_item_id = request.args.get("item_id", type=int)
            valid_item_ids = {choice[0] for choice in form.item_id.choices}
            if requested_item_id in valid_item_ids:
                form.item_id.data = requested_item_id

            requested_vendor_id = request.args.get("vendor_id", type=int)
            valid_vendor_ids = {choice[0] for choice in form.vendor_id.choices}
            if requested_vendor_id in valid_vendor_ids:
                form.vendor_id.data = requested_vendor_id

            requested_unit_id = request.args.get("item_unit_id", type=int)
            valid_unit_ids = {choice[0] for choice in form.item_unit_id.choices}
            if requested_unit_id in valid_unit_ids:
                form.item_unit_id.data = requested_unit_id

    aliases_query = (
        VendorItemAlias.query.options(
            selectinload(VendorItemAlias.vendor),
            selectinload(VendorItemAlias.item),
            selectinload(VendorItemAlias.item_unit),
        )
        .join(Vendor, Vendor.id == VendorItemAlias.vendor_id)
    )
    total_alias_count = aliases_query.count()
    if filter_vendor_id:
        aliases_query = aliases_query.filter(VendorItemAlias.vendor_id == filter_vendor_id)
    if filter_item_id:
        aliases_query = aliases_query.filter(VendorItemAlias.item_id == filter_item_id)
    if filter_query:
        aliases_query = aliases_query.filter(
            or_(
                build_text_match_predicate(
                    VendorItemAlias.vendor_sku, filter_query, "contains"
                ),
                build_text_match_predicate(
                    VendorItemAlias.vendor_description, filter_query, "contains"
                ),
                build_text_match_predicate(
                    VendorItemAlias.pack_size, filter_query, "contains"
                ),
            )
        )
    aliases = (
        aliases_query.order_by(
            Vendor.first_name,
            Vendor.last_name,
            VendorItemAlias.vendor_sku,
            VendorItemAlias.vendor_description,
        ).all()
    )
    filter_vendors = Vendor.query.order_by(Vendor.first_name, Vendor.last_name).all()
    filter_items = Item.query.order_by(Item.name).all()

    if form.validate_on_submit():
        vendor = db.session.get(Vendor, form.vendor_id.data)
        if not vendor:
            flash("Select a valid vendor before saving the alias.", "danger")
            return redirect(list_return_url)

        unit_id = form.item_unit_id.data or None
        if unit_id == 0:
            unit_id = None

        default_cost = (
            float(form.default_cost.data)
            if form.default_cost.data is not None
            else None
        )

        if alias_obj:
            alias_obj.vendor_id = vendor.id
            alias_obj.item_id = form.item_id.data
            alias_obj.item_unit_id = unit_id
            alias_obj.vendor_sku = form.vendor_sku.data or None
            alias_obj.vendor_description = form.vendor_description.data or None
            alias_obj.normalized_description = normalize_vendor_alias_text(
                alias_obj.vendor_description or alias_obj.vendor_sku
            )
            alias_obj.pack_size = form.pack_size.data or None
            alias_obj.default_cost = default_cost
            alias = alias_obj
        else:
            alias = update_or_create_vendor_alias(
                vendor=vendor,
                item_id=form.item_id.data,
                item_unit_id=unit_id,
                vendor_sku=form.vendor_sku.data or None,
                vendor_description=form.vendor_description.data or None,
                pack_size=form.pack_size.data or None,
                default_cost=default_cost,
            )
            db.session.add(alias)

        db.session.commit()
        log_activity(
            f"Saved vendor alias for vendor {vendor.first_name} {vendor.last_name}"
        )
        flash("Vendor alias saved successfully.", "success")
        return redirect(
            _safe_local_return_url(form.return_to.data)
            or list_return_url
        )

    return render_template(
        "admin/vendor_item_aliases.html",
        form=form,
        delete_form=delete_form,
        aliases=aliases,
        editing_alias=alias_obj,
        filter_vendors=filter_vendors,
        filter_items=filter_items,
        filter_vendor_id=filter_vendor_id,
        filter_item_id=filter_item_id,
        filter_query=filter_query,
        filters_active=bool(filter_vendor_id or filter_item_id or filter_query),
        total_alias_count=total_alias_count,
        list_return_url=list_return_url,
        can_manage_vendor_item_aliases=current_user.has_permission(
            "vendor_item_aliases.manage"
        ),
    )


@admin.route(
    "/controlpanel/vendor-item-aliases/<int:alias_id>/delete",
    methods=["POST"],
)
@login_required
def delete_vendor_item_alias(alias_id: int):
    def _safe_local_return_url(value: str | None) -> str | None:
        candidate = (value or "").strip().replace("\\", "")
        if not candidate:
            return None
        parsed = urlparse(candidate)
        if parsed.scheme or parsed.netloc or not candidate.startswith("/"):
            return None
        return candidate

    redirect_target = _safe_local_return_url(request.args.get("next")) or url_for(
        "admin.vendor_item_aliases"
    )
    form = DeleteForm()
    if not form.validate_on_submit():
        flash("Unable to process the delete request.", "danger")
        return redirect(redirect_target)

    alias = db.session.get(VendorItemAlias, alias_id)
    if alias is None:
        flash("Vendor alias not found.", "warning")
        return redirect(redirect_target)

    db.session.delete(alias)
    db.session.commit()
    log_activity("Deleted a vendor item alias via admin panel")
    flash("Vendor alias deleted.", "success")
    return redirect(redirect_target)
