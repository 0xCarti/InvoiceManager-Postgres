from __future__ import annotations

from sqlalchemy.orm import selectinload

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


def can_manage_bulletin(actor: User, sender_id: int) -> bool:
    if getattr(actor, "is_super_admin", False) or user_is_schedule_gm(actor):
        return True
    return actor.id == sender_id


def active_bulletin_receipts_for_user(
    user: User,
    *,
    limit: int | None = None,
) -> list[CommunicationRecipient]:
    receipt_options = (
        selectinload(CommunicationRecipient.communication).selectinload(
            Communication.sender
        ),
        selectinload(CommunicationRecipient.communication).selectinload(
            Communication.department
        ),
    )
    receipts = (
        CommunicationRecipient.query.options(*receipt_options)
        .join(Communication, CommunicationRecipient.communication_id == Communication.id)
        .filter(
            CommunicationRecipient.user_id == user.id,
            Communication.kind == Communication.KIND_BULLETIN,
            Communication.active.is_(True),
        )
        .all()
    )
    receipts = sorted(
        receipts,
        key=lambda receipt: (
            bool(getattr(receipt.communication, "pinned", False)),
            getattr(receipt.communication, "created_at", None),
        ),
        reverse=True,
    )
    if limit is not None:
        return receipts[:limit]
    return receipts


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
