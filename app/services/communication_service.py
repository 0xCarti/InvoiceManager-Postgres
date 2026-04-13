from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy import case
from sqlalchemy.orm import selectinload

from app import db
from app.models import (
    Communication,
    CommunicationRecipient,
    Department,
    User,
    UserDepartmentMembership,
)
from app.services.schedule_service import (
    user_can_manage_department,
    user_can_manage_other_user,
    user_department_ids,
    user_is_schedule_gm,
)


def _user_sort_key(user: User) -> tuple[str, str]:
    return (user.sort_key, (user.email or "").casefold())


def communication_scope_departments(actor: User) -> list[Department]:
    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
        return (
            Department.query.filter(Department.active.is_(True))
            .order_by(Department.name.asc())
            .all()
        )

    department_ids = sorted(user_department_ids(actor))
    if not department_ids:
        return []

    return (
        Department.query.filter(
            Department.active.is_(True),
            Department.id.in_(department_ids),
        )
        .order_by(Department.name.asc())
        .all()
    )


def communication_scope_users(actor: User) -> list[User]:
    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
        users = User.query.filter(User.active.is_(True)).all()
        return sorted(users, key=_user_sort_key)

    department_ids = sorted(user_department_ids(actor))
    scoped: dict[int, User] = {}

    if actor.active:
        scoped[actor.id] = actor

    if not department_ids:
        return sorted(scoped.values(), key=_user_sort_key)

    memberships = (
        UserDepartmentMembership.query.options(
            selectinload(UserDepartmentMembership.user),
        )
        .filter(UserDepartmentMembership.department_id.in_(department_ids))
        .all()
    )

    for membership in memberships:
        user = membership.user
        if user is None or not user.active:
            continue
        if membership.department_id in department_ids:
            scoped[user.id] = user
        if user_can_manage_other_user(actor, user, membership.department_id):
            scoped[user.id] = user

    return sorted(scoped.values(), key=_user_sort_key)


def resolve_communication_recipients(
    actor: User,
    *,
    audience: str,
    user_ids: list[int] | None = None,
    department_id: int | None = None,
    include_actor: bool = False,
) -> list[User]:
    scoped_users = communication_scope_users(actor)
    scoped_users_by_id = {user.id: user for user in scoped_users}

    if audience == "all":
        recipients = list(scoped_users)
    elif audience == "users":
        requested_ids = []
        for user_id in user_ids or []:
            if user_id not in requested_ids:
                requested_ids.append(user_id)
        invalid_ids = [user_id for user_id in requested_ids if user_id not in scoped_users_by_id]
        if invalid_ids:
            raise PermissionError("One or more selected users are outside your messaging scope.")
        recipients = [scoped_users_by_id[user_id] for user_id in requested_ids]
    elif audience == "department":
        if not department_id:
            raise ValueError("Choose a department for that audience.")
        allowed_departments = {
            department.id: department for department in communication_scope_departments(actor)
        }
        if department_id not in allowed_departments:
            raise PermissionError("That department is outside your messaging scope.")
        memberships = (
            UserDepartmentMembership.query.options(
                selectinload(UserDepartmentMembership.user),
            )
            .filter_by(department_id=department_id)
            .all()
        )
        recipients = []
        seen_user_ids: set[int] = set()
        for membership in memberships:
            user = scoped_users_by_id.get(membership.user_id)
            if user is None or user.id in seen_user_ids:
                continue
            recipients.append(user)
            seen_user_ids.add(user.id)
    else:
        raise ValueError("Choose a valid audience.")

    if not include_actor:
        recipients = [user for user in recipients if user.id != actor.id]

    if not recipients:
        raise ValueError("No recipients matched that audience.")

    return recipients


def build_bulletin_audience_snapshot(
    actor: User,
    *,
    audience: str,
) -> dict[str, object] | None:
    """Return persisted audience metadata for dynamic bulletin expansion."""

    if audience != Communication.AUDIENCE_ALL:
        return None

    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
        return {
            "all_users": True,
            "department_ids": [],
        }

    return {
        "all_users": False,
        "department_ids": [
            department.id for department in communication_scope_departments(actor)
        ],
    }


def can_manage_bulletin(actor: User, sender_id: int) -> bool:
    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
        return True
    return actor.id == sender_id


def _normalize_audience_snapshot(raw_value: object) -> dict[str, object]:
    if not isinstance(raw_value, Mapping):
        return {
            "all_users": False,
            "department_ids": [],
        }

    normalized_department_ids: list[int] = []
    seen_ids: set[int] = set()
    raw_department_ids = raw_value.get("department_ids")
    if isinstance(raw_department_ids, list):
        for raw_department_id in raw_department_ids:
            try:
                department_id = int(raw_department_id)
            except (TypeError, ValueError):
                continue
            if department_id <= 0 or department_id in seen_ids:
                continue
            normalized_department_ids.append(department_id)
            seen_ids.add(department_id)

    return {
        "all_users": bool(raw_value.get("all_users")),
        "department_ids": normalized_department_ids,
    }


def _fallback_all_scope_snapshot(communication: Communication) -> dict[str, object]:
    sender = communication.sender or db.session.get(User, communication.sender_id)
    if sender is not None and (
        getattr(sender, "is_super_admin", False) or user_is_schedule_gm(sender)
    ):
        return {
            "all_users": True,
            "department_ids": [],
        }

    recipient_user_ids = {receipt.user_id for receipt in communication.recipients}
    recipient_department_ids = {
        department_id
        for (department_id,) in (
            db.session.query(UserDepartmentMembership.department_id)
            .filter(UserDepartmentMembership.user_id.in_(recipient_user_ids))
            .distinct()
            .all()
        )
    } if recipient_user_ids else set()
    if recipient_department_ids:
        return {
            "all_users": False,
            "department_ids": sorted(recipient_department_ids),
        }

    if sender is None:
        return {
            "all_users": False,
            "department_ids": [],
        }

    return build_bulletin_audience_snapshot(
        sender,
        audience=Communication.AUDIENCE_ALL,
    ) or {
        "all_users": False,
        "department_ids": [],
    }


def _all_scope_snapshot_for_communication(communication: Communication) -> dict[str, object]:
    snapshot = _normalize_audience_snapshot(
        getattr(communication, "audience_snapshot", None)
    )
    if snapshot["all_users"] or snapshot["department_ids"]:
        return snapshot
    return _fallback_all_scope_snapshot(communication)


def _active_user_ids_for_departments(department_ids: set[int]) -> set[int]:
    if not department_ids:
        return set()

    return {
        user_id
        for (user_id,) in (
            db.session.query(UserDepartmentMembership.user_id)
            .join(User, User.id == UserDepartmentMembership.user_id)
            .filter(
                UserDepartmentMembership.department_id.in_(department_ids),
                User.active.is_(True),
            )
            .distinct()
            .all()
        )
    }


def _user_ids_for_dynamic_bulletin(communication: Communication) -> set[int]:
    if not communication.is_bulletin or not communication.active:
        return set()

    if communication.audience_type == Communication.AUDIENCE_DEPARTMENT:
        department_id = int(communication.department_id or 0)
        if department_id <= 0:
            return {communication.sender_id}
        recipient_user_ids = _active_user_ids_for_departments({department_id})
    elif communication.audience_type == Communication.AUDIENCE_ALL:
        snapshot = _all_scope_snapshot_for_communication(communication)
        if snapshot["all_users"]:
            recipient_user_ids = {
                user_id
                for (user_id,) in (
                    db.session.query(User.id)
                    .filter(User.active.is_(True))
                    .all()
                )
            }
        else:
            recipient_user_ids = _active_user_ids_for_departments(
                set(snapshot["department_ids"])
            )
    else:
        return set()

    sender = communication.sender or db.session.get(User, communication.sender_id)
    if sender is not None and sender.active:
        recipient_user_ids.add(sender.id)

    return recipient_user_ids


def _user_matches_dynamic_bulletin(
    user: User,
    communication: Communication,
) -> bool:
    if not getattr(user, "active", False):
        return False

    if communication.sender_id == user.id:
        return True

    if communication.audience_type == Communication.AUDIENCE_DEPARTMENT:
        department_id = int(communication.department_id or 0)
        if department_id <= 0:
            return False
        return department_id in set(user_department_ids(user))

    if communication.audience_type == Communication.AUDIENCE_ALL:
        snapshot = _all_scope_snapshot_for_communication(communication)
        if snapshot["all_users"]:
            return True
        return bool(set(snapshot["department_ids"]) & set(user_department_ids(user)))

    return False


def sync_dynamic_bulletin_recipients(communication: Communication | int) -> int:
    """Create missing receipt rows for users currently targeted by a dynamic bulletin."""

    if isinstance(communication, int):
        communication = db.session.get(Communication, communication)

    if communication is None or not communication.is_bulletin or not communication.active:
        return 0
    if communication.audience_type not in {
        Communication.AUDIENCE_DEPARTMENT,
        Communication.AUDIENCE_ALL,
    }:
        return 0

    recipient_user_ids = _user_ids_for_dynamic_bulletin(communication)
    if not recipient_user_ids:
        return 0

    existing_user_ids = {receipt.user_id for receipt in communication.recipients}
    missing_user_ids = sorted(recipient_user_ids - existing_user_ids)
    if not missing_user_ids:
        return 0

    communication.recipients.extend(
        CommunicationRecipient(user_id=user_id)
        for user_id in missing_user_ids
    )
    db.session.commit()
    return len(missing_user_ids)


def sync_dynamic_bulletin_receipts_for_user(
    user: User,
    *,
    communication_id: int | None = None,
) -> int:
    """Create missing receipt rows for ``user`` across dynamic active bulletins."""

    if not getattr(user, "is_authenticated", False) or not getattr(user, "active", False):
        return 0

    query = (
        Communication.query.options(
            selectinload(Communication.recipients),
            selectinload(Communication.sender),
        )
        .filter(
            Communication.kind == Communication.KIND_BULLETIN,
            Communication.active.is_(True),
            Communication.audience_type.in_(
                (
                    Communication.AUDIENCE_DEPARTMENT,
                    Communication.AUDIENCE_ALL,
                )
            ),
        )
    )
    if communication_id is not None:
        query = query.filter(Communication.id == communication_id)

    missing_receipts_created = 0
    for communication in query.all():
        existing_user_ids = {receipt.user_id for receipt in communication.recipients}
        if user.id in existing_user_ids:
            continue
        if not _user_matches_dynamic_bulletin(user, communication):
            continue
        communication.recipients.append(CommunicationRecipient(user_id=user.id))
        missing_receipts_created += 1

    if missing_receipts_created:
        db.session.commit()

    return missing_receipts_created


def _bulletin_receipt_options(
    *,
    include_recipient_users: bool = False,
):
    options = (
        selectinload(CommunicationRecipient.communication).selectinload(
            Communication.sender
        ),
        selectinload(CommunicationRecipient.communication).selectinload(
            Communication.department
        ),
    )
    if include_recipient_users:
        options += (
            selectinload(CommunicationRecipient.communication)
            .selectinload(Communication.recipients)
            .selectinload(CommunicationRecipient.user),
        )
    return options


def _message_receipt_options():
    return (
        selectinload(CommunicationRecipient.communication).selectinload(
            Communication.sender
        ),
        selectinload(CommunicationRecipient.communication).selectinload(
            Communication.department
        ),
    )


def message_receipts_query_for_user(
    user: User,
    *,
    archived: bool = False,
):
    """Return the base query for message receipts visible to ``user``."""

    query = (
        CommunicationRecipient.query.options(*_message_receipt_options())
        .join(Communication, CommunicationRecipient.communication_id == Communication.id)
        .filter(
            CommunicationRecipient.user_id == user.id,
            Communication.kind == Communication.KIND_MESSAGE,
            CommunicationRecipient.deleted_at.is_(None),
        )
    )

    if archived:
        query = query.filter(CommunicationRecipient.archived_at.is_not(None))
        return query.order_by(
            CommunicationRecipient.archived_at.desc(),
            Communication.created_at.desc(),
            Communication.id.desc(),
        )

    return query.filter(CommunicationRecipient.archived_at.is_(None)).order_by(
        case((CommunicationRecipient.read_at.is_(None), 0), else_=1).asc(),
        Communication.created_at.desc(),
        Communication.id.desc(),
    )


def message_receipts_for_user(
    user: User,
    *,
    archived: bool = False,
    limit: int | None = None,
) -> list[CommunicationRecipient]:
    query = message_receipts_query_for_user(user, archived=archived)
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def message_receipt_for_user(
    user: User,
    receipt_id: int,
    *,
    include_archived: bool = True,
) -> CommunicationRecipient | None:
    query = (
        CommunicationRecipient.query.options(*_message_receipt_options())
        .join(Communication, CommunicationRecipient.communication_id == Communication.id)
        .filter(
            CommunicationRecipient.id == receipt_id,
            CommunicationRecipient.user_id == user.id,
            Communication.kind == Communication.KIND_MESSAGE,
            CommunicationRecipient.deleted_at.is_(None),
        )
    )
    if not include_archived:
        query = query.filter(CommunicationRecipient.archived_at.is_(None))
    return query.first()


def active_bulletin_receipts_query_for_user(
    user: User,
    *,
    include_recipient_users: bool = False,
):
    """Return the base query for active bulletin receipts visible to ``user``."""

    return (
        CommunicationRecipient.query.options(
            *_bulletin_receipt_options(
                include_recipient_users=include_recipient_users,
            )
        )
        .join(Communication, CommunicationRecipient.communication_id == Communication.id)
        .filter(
            CommunicationRecipient.user_id == user.id,
            Communication.kind == Communication.KIND_BULLETIN,
            Communication.active.is_(True),
        )
        .order_by(
            case((CommunicationRecipient.read_at.is_(None), 0), else_=1).asc(),
            Communication.created_at.desc(),
            Communication.id.desc(),
        )
    )


def active_bulletin_receipts_for_user(
    user: User,
    *,
    limit: int | None = None,
    include_recipient_users: bool = False,
) -> list[CommunicationRecipient]:
    query = active_bulletin_receipts_query_for_user(
        user,
        include_recipient_users=include_recipient_users,
    )
    if limit is not None:
        query = query.limit(limit)
    return query.all()


def active_bulletin_receipt_for_user(
    user: User,
    communication_id: int,
    *,
    include_recipient_users: bool = False,
) -> CommunicationRecipient | None:
    """Return a single active bulletin receipt for ``user`` by communication id."""

    receipt = (
        active_bulletin_receipts_query_for_user(
            user,
            include_recipient_users=include_recipient_users,
        )
        .filter(Communication.id == communication_id)
        .first()
    )
    if receipt is not None:
        return receipt

    sync_dynamic_bulletin_receipts_for_user(
        user,
        communication_id=communication_id,
    )
    return (
        active_bulletin_receipts_query_for_user(
            user,
            include_recipient_users=include_recipient_users,
        )
        .filter(Communication.id == communication_id)
        .first()
    )


def visible_message_history(actor: User, *, limit: int = 15) -> list[Communication]:
    if not getattr(actor, "is_authenticated", False):
        return []

    candidate_limit = max(limit * 6, 50)
    query = (
        Communication.query.options(
            selectinload(Communication.sender),
            selectinload(Communication.department),
            selectinload(Communication.recipients).selectinload(
                CommunicationRecipient.user
            ),
        )
        .filter(Communication.kind == Communication.KIND_MESSAGE)
        .order_by(Communication.created_at.desc())
        .limit(candidate_limit)
    )

    if getattr(actor, "is_super_admin", False):
        return query.all()

    scoped_user_ids = {user.id for user in communication_scope_users(actor)}
    visible_messages: list[Communication] = []

    for message in query.all():
        recipient_ids = {receipt.user_id for receipt in message.recipients}
        if not recipient_ids:
            continue
        if message.sender_id not in scoped_user_ids:
            continue
        if not recipient_ids.issubset(scoped_user_ids):
            continue
        visible_messages.append(message)
        if len(visible_messages) >= limit:
            break

    return visible_messages


def user_can_broadcast_to_all(actor: User) -> bool:
    return bool(
        getattr(actor, "is_super_admin", False)
        or user_is_schedule_gm(actor)
        or any(
            user_can_manage_department(actor, department_id)
            for department_id in user_department_ids(actor)
        )
    )
