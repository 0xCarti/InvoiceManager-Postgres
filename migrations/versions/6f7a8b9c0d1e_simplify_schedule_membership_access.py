"""simplify schedule membership access

Revision ID: 6f7a8b9c0d1e
Revises: 5e6f7a8b9c0d
Create Date: 2026-08-06 00:00:00.000000

"""

from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6f7a8b9c0d1e"
down_revision = "5e6f7a8b9c0d"
branch_labels = None
depends_on = None


GLOBAL_SCOPE_PERMISSION = "communications.global_scope"
LEGACY_GM_GROUP_KEY = "legacy_schedule_gm_global_scope"
LEGACY_GM_GROUP_NAME = "Migrated Global Communications Access"
ROLE_SETTING_NAME = "SCHEDULE_MEMBERSHIP_ROLES"


def _normalize_role(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _management_roles(connection) -> set[str]:
    role_names = {"manager", "gm"}
    raw_setting = connection.execute(
        sa.text("SELECT value FROM setting WHERE name = :name"),
        {"name": ROLE_SETTING_NAME},
    ).scalar_one_or_none()
    if not raw_setting:
        return role_names

    try:
        definitions = json.loads(raw_setting)
    except (TypeError, ValueError):
        return role_names
    if not isinstance(definitions, list):
        return role_names

    seen_names: set[str] = set()
    for definition in definitions:
        if isinstance(definition, str):
            role_name = _normalize_role(definition)
            is_management = role_name in {"manager", "gm"}
        elif isinstance(definition, dict):
            role_name = _normalize_role(definition.get("name"))
            is_management = bool(definition.get("is_management"))
        else:
            continue

        if not role_name or role_name in seen_names:
            continue
        seen_names.add(role_name)
        if is_management:
            role_names.add(role_name)
        else:
            role_names.discard(role_name)
    return role_names


def _permission_id(connection) -> int:
    permission = sa.table(
        "permission",
        sa.column("id", sa.Integer),
        sa.column("code", sa.String),
        sa.column("category", sa.String),
        sa.column("label", sa.String),
        sa.column("description", sa.Text),
    )
    existing_id = connection.execute(
        sa.select(permission.c.id).where(permission.c.code == GLOBAL_SCOPE_PERMISSION)
    ).scalar_one_or_none()
    if existing_id is not None:
        return int(existing_id)

    return int(
        connection.execute(
            permission.insert()
            .values(
                code=GLOBAL_SCOPE_PERMISSION,
                category="communications",
                label="Global Communications Scope",
                description=(
                    "Access communication recipients and bulletins across the "
                    "organization."
                ),
            )
            .returning(permission.c.id)
        ).scalar_one()
    )


def _available_group_name(connection) -> str:
    group = sa.table(
        "permission_group",
        sa.column("name", sa.String),
    )
    candidate = LEGACY_GM_GROUP_NAME
    suffix = 2
    while connection.execute(
        sa.select(group.c.name).where(group.c.name == candidate)
    ).scalar_one_or_none() is not None:
        candidate = f"{LEGACY_GM_GROUP_NAME} {suffix}"
        suffix += 1
    return candidate


def _legacy_gm_group_id(connection) -> int:
    group = sa.table(
        "permission_group",
        sa.column("id", sa.Integer),
        sa.column("key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_system", sa.Boolean),
    )
    existing_id = connection.execute(
        sa.select(group.c.id).where(group.c.key == LEGACY_GM_GROUP_KEY)
    ).scalar_one_or_none()
    if existing_id is not None:
        return int(existing_id)

    return int(
        connection.execute(
            group.insert()
            .values(
                key=LEGACY_GM_GROUP_KEY,
                name=_available_group_name(connection),
                description=(
                    "Preserves organization-wide communication scope from the "
                    "previous scheduling access model."
                ),
                is_system=True,
            )
            .returning(group.c.id)
        ).scalar_one()
    )


def _link_if_missing(
    connection,
    table,
    values: dict[str, int],
) -> None:
    conditions = [table.c[column_name] == value for column_name, value in values.items()]
    exists = connection.execute(
        sa.select(sa.literal(1)).select_from(table).where(*conditions).limit(1)
    ).scalar_one_or_none()
    if exists is None:
        connection.execute(table.insert().values(**values))


def upgrade() -> None:
    op.add_column(
        "schedule_user_department_membership",
        sa.Column(
            "can_manage_department",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    connection = op.get_bind()
    membership = sa.table(
        "schedule_user_department_membership",
        sa.column("id", sa.Integer),
        sa.column("user_id", sa.Integer),
        sa.column("role", sa.String),
        sa.column("can_manage_department", sa.Boolean),
    )
    membership_rows = connection.execute(
        sa.select(membership.c.id, membership.c.user_id, membership.c.role)
    ).mappings()
    management_roles = _management_roles(connection)
    management_membership_ids: list[int] = []
    former_gm_user_ids: set[int] = set()
    for row in membership_rows:
        normalized_role = _normalize_role(row["role"])
        if normalized_role in management_roles:
            management_membership_ids.append(int(row["id"]))
        if normalized_role == "gm":
            former_gm_user_ids.add(int(row["user_id"]))

    if management_membership_ids:
        connection.execute(
            membership.update()
            .where(membership.c.id.in_(management_membership_ids))
            .values(can_manage_department=True)
        )

    permission_id = _permission_id(connection)
    if not former_gm_user_ids:
        return

    group_id = _legacy_gm_group_id(connection)
    group_permissions = sa.table(
        "permission_group_permissions",
        sa.column("permission_group_id", sa.Integer),
        sa.column("permission_id", sa.Integer),
    )
    user_groups = sa.table(
        "user_permission_groups",
        sa.column("user_id", sa.Integer),
        sa.column("permission_group_id", sa.Integer),
    )
    _link_if_missing(
        connection,
        group_permissions,
        {"permission_group_id": group_id, "permission_id": permission_id},
    )
    for user_id in sorted(former_gm_user_ids):
        _link_if_missing(
            connection,
            user_groups,
            {"user_id": user_id, "permission_group_id": group_id},
        )


def downgrade() -> None:
    # Keep the permission and migration group intact. They are inert under the
    # legacy role-based code, and removing them could destroy assignments made
    # deliberately after this migration was deployed.
    op.drop_column(
        "schedule_user_department_membership",
        "can_manage_department",
    )
