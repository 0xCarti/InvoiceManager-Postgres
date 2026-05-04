from __future__ import annotations

from typing import Iterable

from flask import current_app
from sqlalchemy import or_

from app.models import User
from app.utils.email import send_email
from app.utils.sms import send_sms


NOTIFICATION_CATEGORY_FIELDS: dict[str, tuple[str | None, str | None]] = {
    "transfers": ("notify_transfers_email", "notify_transfers"),
    "purchase_orders": (
        "notify_purchase_orders_email",
        "notify_purchase_orders_text",
    ),
    "events": ("notify_events_email", "notify_events_text"),
    "users": ("notify_users_email", "notify_users_text"),
    "messages": ("notify_messages_email", "notify_messages_text"),
    "bulletins": ("notify_bulletins_email", "notify_bulletins_text"),
    "locations": ("notify_locations_email", "notify_locations_text"),
}


def operational_notification_fields() -> tuple[str, ...]:
    fields: list[str] = []
    for email_field, text_field in NOTIFICATION_CATEGORY_FIELDS.values():
        if email_field:
            fields.append(email_field)
        if text_field:
            fields.append(text_field)
    return tuple(fields)


def _safe_send_email(to_address: str, subject: str, body: str) -> None:
    try:
        send_email(to_address, subject, body)
    except Exception as exc:  # pragma: no cover - defensive logging
        current_app.logger.warning(
            "Notification email failed for %s: %s", to_address, exc
        )


def _safe_send_sms(to_number: str, body: str) -> None:
    try:
        send_sms(to_number, body)
    except Exception as exc:  # pragma: no cover - defensive logging
        current_app.logger.warning(
            "Notification SMS failed for %s: %s", to_number, exc
        )


def _category_fields(category: str) -> tuple[str | None, str | None]:
    try:
        return NOTIFICATION_CATEGORY_FIELDS[category]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Unknown notification category: {category}") from exc


def _is_channel_enabled(user: User, field_name: str | None) -> bool:
    return bool(field_name and getattr(user, field_name, False))


def deliver_user_notification(
    user: User,
    *,
    email_enabled: bool,
    text_enabled: bool,
    subject: str,
    body: str,
    sms_body: str | None = None,
) -> None:
    if not getattr(user, "active", False):
        return
    if email_enabled:
        _safe_send_email(user.email, subject, body)
    if text_enabled and user.phone_number:
        _safe_send_sms(user.phone_number, (sms_body or body)[:320])


def notify_users_for_category(
    *,
    category: str,
    subject: str,
    body: str,
    sms_body: str | None = None,
    recipients: Iterable[User] | None = None,
    exclude_user_ids: Iterable[int] | None = None,
) -> None:
    email_field, text_field = _category_fields(category)
    excluded_ids = {
        int(user_id)
        for user_id in (exclude_user_ids or [])
        if user_id is not None
    }

    if recipients is None:
        filters = []
        if email_field:
            filters.append(getattr(User, email_field).is_(True))
        if text_field:
            filters.append(getattr(User, text_field).is_(True))
        if not filters:
            return
        query = User.query.filter(User.active.is_(True))
        if excluded_ids:
            query = query.filter(~User.id.in_(excluded_ids))
        recipients = query.filter(or_(*filters)).all()

    for user in recipients:
        if user is None or user.id in excluded_ids:
            continue
        deliver_user_notification(
            user,
            email_enabled=_is_channel_enabled(user, email_field),
            text_enabled=_is_channel_enabled(user, text_field),
            subject=subject,
            body=body,
            sms_body=sms_body,
        )
