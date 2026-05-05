from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
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

NOTIFICATION_CHANNELS = ("email", "text")


@dataclass(frozen=True)
class NotificationEventDefinition:
    key: str
    group_key: str
    group_label: str
    label: str
    description: str
    legacy_category: str


NOTIFICATION_EVENT_DEFINITIONS: tuple[NotificationEventDefinition, ...] = (
    NotificationEventDefinition(
        key="transfer_created",
        group_key="transfers",
        group_label="Transfers",
        label="Transfer created",
        description="When a new transfer is created.",
        legacy_category="transfers",
    ),
    NotificationEventDefinition(
        key="transfer_completed",
        group_key="transfers",
        group_label="Transfers",
        label="Transfer completed",
        description="When a transfer is marked complete.",
        legacy_category="transfers",
    ),
    NotificationEventDefinition(
        key="transfer_reopened",
        group_key="transfers",
        group_label="Transfers",
        label="Transfer reopened",
        description="When a completed transfer is reopened.",
        legacy_category="transfers",
    ),
    NotificationEventDefinition(
        key="transfer_updated",
        group_key="transfers",
        group_label="Transfers",
        label="Transfer updated and reopened",
        description="When editing a transfer reopens it for reconciliation.",
        legacy_category="transfers",
    ),
    NotificationEventDefinition(
        key="purchase_order_created",
        group_key="purchase_orders",
        group_label="Purchase Orders",
        label="Purchase order created",
        description="When a new purchase order is created.",
        legacy_category="purchase_orders",
    ),
    NotificationEventDefinition(
        key="purchase_order_updated",
        group_key="purchase_orders",
        group_label="Purchase Orders",
        label="Purchase order updated",
        description="When a purchase order is edited.",
        legacy_category="purchase_orders",
    ),
    NotificationEventDefinition(
        key="purchase_order_marked_ordered",
        group_key="purchase_orders",
        group_label="Purchase Orders",
        label="Purchase order marked as ordered",
        description="When a purchase order is submitted to the vendor.",
        legacy_category="purchase_orders",
    ),
    NotificationEventDefinition(
        key="purchase_order_received",
        group_key="purchase_orders",
        group_label="Purchase Orders",
        label="Purchase order received",
        description="When an invoice is received against a purchase order.",
        legacy_category="purchase_orders",
    ),
    NotificationEventDefinition(
        key="purchase_order_reversed",
        group_key="purchase_orders",
        group_label="Purchase Orders",
        label="Purchase order reversed",
        description="When a received purchase order is reversed.",
        legacy_category="purchase_orders",
    ),
    NotificationEventDefinition(
        key="event_created",
        group_key="events",
        group_label="Events",
        label="Event created",
        description="When a new event is created.",
        legacy_category="events",
    ),
    NotificationEventDefinition(
        key="event_updated",
        group_key="events",
        group_label="Events",
        label="Event updated",
        description="When core event details are edited.",
        legacy_category="events",
    ),
    NotificationEventDefinition(
        key="event_locations_assigned",
        group_key="events",
        group_label="Events",
        label="Event locations assigned",
        description="When locations are assigned to an event.",
        legacy_category="events",
    ),
    NotificationEventDefinition(
        key="event_closed",
        group_key="events",
        group_label="Events",
        label="Event closed",
        description="When an event is closed.",
        legacy_category="events",
    ),
    NotificationEventDefinition(
        key="event_deleted",
        group_key="events",
        group_label="Events",
        label="Event deleted",
        description="When an event is deleted.",
        legacy_category="events",
    ),
    NotificationEventDefinition(
        key="location_created",
        group_key="locations",
        group_label="Locations",
        label="Location created",
        description="When a new location is added.",
        legacy_category="locations",
    ),
    NotificationEventDefinition(
        key="location_updated",
        group_key="locations",
        group_label="Locations",
        label="Location updated",
        description="When location details are edited.",
        legacy_category="locations",
    ),
    NotificationEventDefinition(
        key="location_archived",
        group_key="locations",
        group_label="Locations",
        label="Location archived",
        description="When a location is archived.",
        legacy_category="locations",
    ),
    NotificationEventDefinition(
        key="user_invited",
        group_key="users",
        group_label="Users",
        label="User invited",
        description="When a new user invitation is sent.",
        legacy_category="users",
    ),
    NotificationEventDefinition(
        key="user_activated",
        group_key="users",
        group_label="Users",
        label="User activated",
        description="When a user account is activated.",
        legacy_category="users",
    ),
    NotificationEventDefinition(
        key="user_deactivated",
        group_key="users",
        group_label="Users",
        label="User deactivated",
        description="When a user account is deactivated.",
        legacy_category="users",
    ),
    NotificationEventDefinition(
        key="user_access_updated",
        group_key="users",
        group_label="Users",
        label="User access updated",
        description="When admin access or permissions are changed.",
        legacy_category="users",
    ),
    NotificationEventDefinition(
        key="user_pending_invite_deleted",
        group_key="users",
        group_label="Users",
        label="Pending invite deleted",
        description="When a pending user invitation is deleted.",
        legacy_category="users",
    ),
    NotificationEventDefinition(
        key="user_archived",
        group_key="users",
        group_label="Users",
        label="User archived",
        description="When a user account is archived.",
        legacy_category="users",
    ),
    NotificationEventDefinition(
        key="message_received",
        group_key="communications",
        group_label="Communications",
        label="Direct message received",
        description="When someone sends you a direct message.",
        legacy_category="messages",
    ),
    NotificationEventDefinition(
        key="bulletin_posted",
        group_key="communications",
        group_label="Communications",
        label="Bulletin posted",
        description="When a new bulletin is posted.",
        legacy_category="bulletins",
    ),
)

NOTIFICATION_EVENT_DEFINITIONS_BY_KEY = {
    definition.key: definition for definition in NOTIFICATION_EVENT_DEFINITIONS
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


def notification_event_definitions() -> tuple[NotificationEventDefinition, ...]:
    return NOTIFICATION_EVENT_DEFINITIONS


def notification_event_definition(key: str) -> NotificationEventDefinition:
    try:
        return NOTIFICATION_EVENT_DEFINITIONS_BY_KEY[key]
    except KeyError as exc:  # pragma: no cover - defensive guard
        raise ValueError(f"Unknown notification event: {key}") from exc


def operational_notification_groups():
    grouped: OrderedDict[str, dict[str, object]] = OrderedDict()
    for definition in NOTIFICATION_EVENT_DEFINITIONS:
        group = grouped.setdefault(
            definition.group_key,
            {
                "key": definition.group_key,
                "label": definition.group_label,
                "events": [],
            },
        )
        group["events"].append(definition)
    return tuple(grouped.values())


def notification_preference_input_name(event_key: str, channel: str) -> str:
    if channel not in NOTIFICATION_CHANNELS:
        raise ValueError(f"Unknown notification channel: {channel}")
    return f"notification_pref__{event_key}__{channel}"


def notification_preference_input_id(event_key: str, channel: str) -> str:
    return notification_preference_input_name(event_key, channel).replace("_", "-")


def _normalize_notification_preferences(data) -> dict[str, dict[str, bool]]:
    normalized: dict[str, dict[str, bool]] = {}
    if not isinstance(data, dict):
        return normalized
    for definition in NOTIFICATION_EVENT_DEFINITIONS:
        value = data.get(definition.key)
        if not isinstance(value, dict):
            continue
        normalized[definition.key] = {
            channel: bool(value.get(channel, False)) for channel in NOTIFICATION_CHANNELS
        }
    return normalized


def resolved_notification_preferences(user: User) -> dict[str, dict[str, bool]]:
    stored_preferences = _normalize_notification_preferences(
        getattr(user, "notification_preferences", None)
    )
    resolved: dict[str, dict[str, bool]] = {}
    for definition in NOTIFICATION_EVENT_DEFINITIONS:
        explicit = stored_preferences.get(definition.key)
        if explicit is not None:
            resolved[definition.key] = dict(explicit)
            continue
        email_field, text_field = _category_fields(definition.legacy_category)
        resolved[definition.key] = {
            "email": _is_channel_enabled(user, email_field),
            "text": _is_channel_enabled(user, text_field),
        }
    return resolved


def notification_channel_enabled(user: User, *, event_key: str, channel: str) -> bool:
    if channel not in NOTIFICATION_CHANNELS:
        raise ValueError(f"Unknown notification channel: {channel}")
    return bool(resolved_notification_preferences(user).get(event_key, {}).get(channel, False))


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


def notify_users_for_event(
    *,
    event_key: str,
    subject: str,
    body: str,
    sms_body: str | None = None,
    recipients: Iterable[User] | None = None,
    exclude_user_ids: Iterable[int] | None = None,
) -> None:
    notification_event_definition(event_key)
    excluded_ids = {
        int(user_id)
        for user_id in (exclude_user_ids or [])
        if user_id is not None
    }

    if recipients is None:
        query = User.query.filter(User.active.is_(True))
        if excluded_ids:
            query = query.filter(~User.id.in_(excluded_ids))
        recipients = query.all()

    for user in recipients:
        if user is None or user.id in excluded_ids:
            continue
        deliver_user_notification(
            user,
            email_enabled=notification_channel_enabled(
                user, event_key=event_key, channel="email"
            ),
            text_enabled=notification_channel_enabled(
                user, event_key=event_key, channel="text"
            ),
            subject=subject,
            body=body,
            sms_body=sms_body,
        )
