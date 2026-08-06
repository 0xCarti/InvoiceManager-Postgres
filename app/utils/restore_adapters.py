"""Version-aware backup restore adapters.

Adapters translate legacy backup table rows into row dictionaries that match
the current SQLAlchemy model schema used by restore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy import MetaData, Table
from sqlalchemy.engine import Connection


def _parse_marker(marker: str | None) -> tuple[int, int] | None:
    if not marker:
        return None
    raw = marker.strip()
    if not raw:
        return None
    parts = raw.split(".")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None


def _marker_leq(marker: str | None, upper_bound: str) -> bool:
    parsed_marker = _parse_marker(marker)
    parsed_upper = _parse_marker(upper_bound)
    if parsed_marker is None or parsed_upper is None:
        return False
    return parsed_marker <= parsed_upper


@dataclass
class RestoreAdapterContext:
    backup_metadata: MetaData
    schema_marker: str | None


@dataclass
class RestoreAdapterResult:
    rows: list[dict]
    transformed_count: int = 0
    unresolved_rows: list[dict] | None = None
    metrics: dict[str, int] | None = None


@dataclass
class RestorePostLoadContext:
    backup_metadata: MetaData
    schema_marker: str | None
    target_metadata: MetaData
    target_connection: Connection


@dataclass
class RestorePostLoadResult:
    transformed_count: int = 0
    affected_tables: set[str] | None = None
    metrics: dict[str, int] | None = None


class RestoreTableAdapter:
    """Adapter interface for table-level legacy backup transforms."""

    table_name: str

    def applies_to(
        self,
        *,
        table: Table,
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> bool:
        raise NotImplementedError

    def adapt(
        self,
        *,
        table: Table,
        rows: Iterable[dict],
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> RestoreAdapterResult:
        raise NotImplementedError


class RestorePostLoadHook:
    """Hook interface for transforms that depend on several restored tables."""

    def applies_to(self, *, context: RestorePostLoadContext) -> bool:
        raise NotImplementedError

    def run(self, *, context: RestorePostLoadContext) -> RestorePostLoadResult:
        raise NotImplementedError


class PurchaseInvoiceItemLegacyGlCodeAdapter(RestoreTableAdapter):
    """Map legacy `gl_code_id` to `purchase_gl_code_id` for invoice items."""

    table_name = "purchase_invoice_item"
    _legacy_upper_marker = "2025.12"

    def applies_to(
        self,
        *,
        table: Table,
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> bool:
        if table.name != self.table_name:
            return False
        if "gl_code_id" not in backup_columns:
            return False
        if "purchase_gl_code_id" in backup_columns:
            return False
        # Schema signature is primary signal; marker match is secondary support.
        marker = context.schema_marker
        return marker is None or _marker_leq(marker, self._legacy_upper_marker)

    def adapt(
        self,
        *,
        table: Table,
        rows: Iterable[dict],
        backup_columns: set[str],
        context: RestoreAdapterContext,
    ) -> RestoreAdapterResult:
        adapted_rows: list[dict] = []
        remapped = 0
        for row in rows:
            record = dict(row)
            if "purchase_gl_code_id" not in record and "gl_code_id" in record:
                record["purchase_gl_code_id"] = record.get("gl_code_id")
                remapped += 1
            record.pop("gl_code_id", None)
            adapted_rows.append(record)
        return RestoreAdapterResult(
            rows=adapted_rows,
            transformed_count=remapped,
            metrics={"legacy_gl_code_id_remapped": remapped},
        )


class LegacyScheduleAccessPostLoadHook(RestorePostLoadHook):
    """Preserve scheduling access when restoring a role-based backup."""

    membership_table_name = "schedule_user_department_membership"
    global_scope_permission = "communications.global_scope"
    legacy_gm_group_key = "legacy_schedule_gm_global_scope"
    legacy_gm_group_name = "Migrated Global Communications Access"
    role_setting_name = "SCHEDULE_MEMBERSHIP_ROLES"

    @staticmethod
    def _normalize_role(value: object) -> str:
        return " ".join(str(value or "").strip().lower().split())

    def _management_roles(self, context: RestorePostLoadContext) -> set[str]:
        role_names = {"manager", "gm"}
        setting = context.target_metadata.tables["setting"]
        raw_setting = context.target_connection.execute(
            sa.select(setting.c.value).where(
                setting.c.name == self.role_setting_name
            )
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
                role_name = self._normalize_role(definition)
                is_management = role_name in {"manager", "gm"}
            elif isinstance(definition, dict):
                role_name = self._normalize_role(definition.get("name"))
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

    @staticmethod
    def _link_if_missing(
        connection: Connection,
        table: Table,
        values: dict[str, int],
    ) -> bool:
        conditions = [
            table.c[column_name] == value
            for column_name, value in values.items()
        ]
        exists = connection.execute(
            sa.select(sa.literal(1))
            .select_from(table)
            .where(*conditions)
            .limit(1)
        ).scalar_one_or_none()
        if exists is not None:
            return False
        connection.execute(table.insert().values(**values))
        return True

    def _permission_id(self, context: RestorePostLoadContext) -> tuple[int, bool]:
        permission = context.target_metadata.tables["permission"]
        existing_id = context.target_connection.execute(
            sa.select(permission.c.id).where(
                permission.c.code == self.global_scope_permission
            )
        ).scalar_one_or_none()
        if existing_id is not None:
            return int(existing_id), False

        permission_id = int(
            context.target_connection.execute(
                sa.select(sa.func.coalesce(sa.func.max(permission.c.id), 0) + 1)
            ).scalar_one()
        )
        context.target_connection.execute(
            permission.insert().values(
                id=permission_id,
                code=self.global_scope_permission,
                category="communications",
                label="Global Communications Scope",
                description=(
                    "Access communication recipients and bulletins across the "
                    "organization."
                ),
            )
        )
        return permission_id, True

    def _available_group_name(self, context: RestorePostLoadContext) -> str:
        group = context.target_metadata.tables["permission_group"]
        candidate = self.legacy_gm_group_name
        suffix = 2
        while context.target_connection.execute(
            sa.select(group.c.id).where(group.c.name == candidate)
        ).scalar_one_or_none() is not None:
            candidate = f"{self.legacy_gm_group_name} {suffix}"
            suffix += 1
        return candidate

    def _group_id(self, context: RestorePostLoadContext) -> tuple[int, bool]:
        group = context.target_metadata.tables["permission_group"]
        existing_id = context.target_connection.execute(
            sa.select(group.c.id).where(
                group.c.key == self.legacy_gm_group_key
            )
        ).scalar_one_or_none()
        if existing_id is not None:
            return int(existing_id), False

        group_id = int(
            context.target_connection.execute(
                sa.select(sa.func.coalesce(sa.func.max(group.c.id), 0) + 1)
            ).scalar_one()
        )
        context.target_connection.execute(
            group.insert().values(
                id=group_id,
                key=self.legacy_gm_group_key,
                name=self._available_group_name(context),
                description=(
                    "Preserves organization-wide communication scope from the "
                    "previous scheduling access model."
                ),
                is_system=True,
            )
        )
        return group_id, True

    def applies_to(self, *, context: RestorePostLoadContext) -> bool:
        backup_membership = context.backup_metadata.tables.get(
            self.membership_table_name
        )
        target_membership = context.target_metadata.tables.get(
            self.membership_table_name
        )
        if backup_membership is None or target_membership is None:
            return False
        required_target_tables = {
            "setting",
            "permission",
            "permission_group",
            "permission_group_permissions",
            "user_permission_groups",
        }
        return bool(
            "role" in backup_membership.c
            and "can_manage_department" not in backup_membership.c
            and "role" in target_membership.c
            and "can_manage_department" in target_membership.c
            and required_target_tables.issubset(context.target_metadata.tables)
        )

    def run(self, *, context: RestorePostLoadContext) -> RestorePostLoadResult:
        membership = context.target_metadata.tables[self.membership_table_name]
        membership_rows = context.target_connection.execute(
            sa.select(membership.c.id, membership.c.user_id, membership.c.role)
        ).mappings()
        management_roles = self._management_roles(context)
        management_membership_ids: list[int] = []
        former_gm_user_ids: set[int] = set()
        for row in membership_rows:
            normalized_role = self._normalize_role(row["role"])
            if normalized_role in management_roles:
                management_membership_ids.append(int(row["id"]))
            if normalized_role == "gm":
                former_gm_user_ids.add(int(row["user_id"]))

        context.target_connection.execute(
            membership.update().values(can_manage_department=False)
        )
        if management_membership_ids:
            context.target_connection.execute(
                membership.update()
                .where(membership.c.id.in_(management_membership_ids))
                .values(can_manage_department=True)
            )

        permission_id, permission_created = self._permission_id(context)
        group_created = False
        permission_link_added = False
        user_links_added = 0
        if former_gm_user_ids:
            group_id, group_created = self._group_id(context)
            group_permissions = context.target_metadata.tables[
                "permission_group_permissions"
            ]
            user_groups = context.target_metadata.tables["user_permission_groups"]
            permission_link_added = self._link_if_missing(
                context.target_connection,
                group_permissions,
                {
                    "permission_group_id": group_id,
                    "permission_id": permission_id,
                },
            )
            for user_id in sorted(former_gm_user_ids):
                if self._link_if_missing(
                    context.target_connection,
                    user_groups,
                    {"user_id": user_id, "permission_group_id": group_id},
                ):
                    user_links_added += 1

        metrics = {
            "management_memberships_backfilled": len(
                management_membership_ids
            ),
            "global_scope_permission_created": int(permission_created),
            "legacy_gm_group_created": int(group_created),
            "legacy_gm_permission_link_added": int(permission_link_added),
            "legacy_gm_user_links_added": user_links_added,
        }
        affected_tables = {self.membership_table_name}
        if permission_created:
            affected_tables.add("permission")
        if group_created:
            affected_tables.add("permission_group")
        if permission_link_added:
            affected_tables.add("permission_group_permissions")
        if user_links_added:
            affected_tables.add("user_permission_groups")
        return RestorePostLoadResult(
            transformed_count=sum(metrics.values()),
            affected_tables=affected_tables,
            metrics=metrics,
        )


RESTORE_TABLE_ADAPTERS: tuple[RestoreTableAdapter, ...] = (
    PurchaseInvoiceItemLegacyGlCodeAdapter(),
)

RESTORE_POST_LOAD_HOOKS: tuple[RestorePostLoadHook, ...] = (
    LegacyScheduleAccessPostLoadHook(),
)


def apply_restore_adapters(
    *,
    table: Table,
    backup_columns: set[str],
    rows: list[dict],
    context: RestoreAdapterContext,
) -> RestoreAdapterResult:
    adapted_rows = list(rows)
    transformed_count = 0
    metrics: dict[str, int] = {}
    unresolved_rows: list[dict] = []

    for adapter in RESTORE_TABLE_ADAPTERS:
        if not adapter.applies_to(
            table=table,
            backup_columns=backup_columns,
            context=context,
        ):
            continue
        result = adapter.adapt(
            table=table,
            rows=adapted_rows,
            backup_columns=backup_columns,
            context=context,
        )
        adapted_rows = result.rows
        transformed_count += result.transformed_count
        for key, value in (result.metrics or {}).items():
            metrics[key] = metrics.get(key, 0) + value
        unresolved_rows.extend(result.unresolved_rows or [])

    return RestoreAdapterResult(
        rows=adapted_rows,
        transformed_count=transformed_count,
        unresolved_rows=unresolved_rows,
        metrics=metrics or None,
    )


def apply_restore_post_load_hooks(
    *,
    context: RestorePostLoadContext,
) -> RestorePostLoadResult:
    transformed_count = 0
    affected_tables: set[str] = set()
    metrics: dict[str, int] = {}

    for hook in RESTORE_POST_LOAD_HOOKS:
        if not hook.applies_to(context=context):
            continue
        result = hook.run(context=context)
        transformed_count += result.transformed_count
        affected_tables.update(result.affected_tables or set())
        for key, value in (result.metrics or {}).items():
            metrics[key] = metrics.get(key, 0) + value

    return RestorePostLoadResult(
        transformed_count=transformed_count,
        affected_tables=affected_tables or None,
        metrics=metrics or None,
    )
