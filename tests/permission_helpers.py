from __future__ import annotations

from app import db
from app.models import Permission, PermissionGroup, User


EVENT_PERMISSION_CODES = (
    "events.view",
    "events.create",
    "events.edit",
    "events.delete",
    "events.manage_locations",
    "events.manage_sales",
    "events.confirm_locations",
    "events.close",
    "events.reports",
)

ITEM_PERMISSION_CODES = (
    "items.view",
    "items.create",
    "items.edit",
)

PRODUCT_PERMISSION_CODES = (
    "products.create",
    "products.view",
)

GL_CODE_PERMISSION_CODES = (
    "gl_codes.view",
)

IMPORT_PERMISSION_CODES = (
    "imports.run",
    "imports.view",
)

PURCHASE_PERMISSION_CODES = (
    "purchase_orders.view",
    "purchase_orders.create",
    "purchase_orders.edit",
    "purchase_orders.upload",
    "purchase_orders.resolve_vendor_items",
    "purchase_orders.recommendations",
    "purchase_invoices.view",
)


def grant_permissions(
    user: User,
    *codes: str,
    group_name: str,
    description: str,
) -> PermissionGroup:
    unique_codes = tuple(dict.fromkeys(code for code in codes if code))
    permissions = Permission.query.filter(Permission.code.in_(unique_codes)).all()
    found_codes = {permission.code for permission in permissions}
    missing_codes = sorted(set(unique_codes) - found_codes)
    if missing_codes:
        raise AssertionError(f"Unknown permission codes for test helper: {missing_codes}")

    group = PermissionGroup(name=group_name, description=description)
    group.permissions = permissions
    db.session.add(group)
    db.session.flush()
    user.permission_groups.append(group)
    user.invalidate_permission_cache()
    db.session.commit()
    return group


def make_super_admin(user: User) -> User:
    user.is_admin = True
    user.active = True
    user.invalidate_permission_cache()
    db.session.commit()
    return user


def grant_event_permissions(
    user: User, *, include_product_create: bool = True
) -> PermissionGroup:
    codes = list(EVENT_PERMISSION_CODES)
    if include_product_create:
        codes.append("products.create")
    return grant_permissions(
        user,
        *codes,
        group_name=f"Event Test Group {user.email}",
        description="Test permissions for event workflows.",
    )


def grant_item_permissions(user: User) -> PermissionGroup:
    return grant_permissions(
        user,
        *ITEM_PERMISSION_CODES,
        group_name=f"Item Test Group {user.email}",
        description="Test permissions for item workflows.",
    )


def grant_product_permissions(user: User) -> PermissionGroup:
    return grant_permissions(
        user,
        *PRODUCT_PERMISSION_CODES,
        group_name=f"Product Test Group {user.email}",
        description="Test permissions for product workflows.",
    )


def grant_gl_code_permissions(user: User) -> PermissionGroup:
    return grant_permissions(
        user,
        *GL_CODE_PERMISSION_CODES,
        group_name=f"GL Code Test Group {user.email}",
        description="Test permissions for GL code workflows.",
    )


def grant_import_permissions(user: User) -> PermissionGroup:
    return grant_permissions(
        user,
        *IMPORT_PERMISSION_CODES,
        group_name=f"Import Test Group {user.email}",
        description="Test permissions for import workflows.",
    )


def grant_purchase_permissions(user: User) -> PermissionGroup:
    return grant_permissions(
        user,
        *PURCHASE_PERMISSION_CODES,
        group_name=f"Purchase Test Group {user.email}",
        description="Test permissions for purchase workflows.",
    )


def grant_item_workflow_permissions(user: User) -> PermissionGroup:
    return grant_permissions(
        user,
        "items.view",
        "items.create",
        "items.edit",
        "items.delete",
        "products.view",
        "products.create",
        "products.edit",
        "products.manage_recipe",
        "gl_codes.view",
        "locations.view",
        "locations.create",
        "locations.manage_items",
        "purchase_orders.view",
        "customers.view",
        "vendors.view",
        "transfers.view",
        "transfers.create",
        "transfers.edit",
        "transfers.complete",
        "invoices.view",
        "invoices.create",
        "reports.purchase_cost_forecast",
        group_name=f"Item Workflow Test Group {user.email}",
        description="Test permissions for item and transfer workflows.",
    )
