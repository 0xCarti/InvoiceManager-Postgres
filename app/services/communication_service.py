from __future__ import annotations

from sqlalchemy.orm import selectinload

from app.models import Department, User, UserDepartmentMembership
from app.services.schedule_service import (
    user_can_manage_department,
    user_can_manage_other_user,
    user_department_ids,
    user_is_schedule_gm,
)


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
        return (
            User.query.filter(User.active.is_(True))
            .order_by(User.email.asc())
            .all()
        )

    department_ids = sorted(user_department_ids(actor))
    scoped: dict[int, User] = {}

    if actor.active:
        scoped[actor.id] = actor

    if not department_ids:
        return sorted(scoped.values(), key=lambda user: user.email.lower())

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

    return sorted(scoped.values(), key=lambda user: user.email.lower())


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


def user_can_broadcast_to_all(actor: User) -> bool:
    return bool(
        getattr(actor, "is_super_admin", False)
        or user_is_schedule_gm(actor)
        or any(
            user_can_manage_department(actor, department_id)
            for department_id in user_department_ids(actor)
        )
    )
