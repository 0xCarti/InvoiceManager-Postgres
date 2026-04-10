import os

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Item,
    Location,
    Permission,
    PermissionGroup,
    Product,
    Setting,
    TerminalSaleLocationAlias,
    TerminalSaleProductAlias,
    User,
    Vendor,
    VendorItemAlias,
)
from app.permissions import sync_permission_data
from tests.permission_helpers import grant_permissions
from tests.utils import login


def test_new_user_does_not_receive_default_permission_group(app):
    with app.app_context():
        user = User(
            email="defaultgroup@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

        db.session.refresh(user)
        assert user.permission_groups == []


def test_permission_sync_removes_legacy_full_access_group(app):
    with app.app_context():
        user = User(
            email="legacygroup@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        legacy_group = PermissionGroup(
            key="legacy_full_app_access",
            name="Full App Access",
            description="Legacy system group.",
            is_system=True,
        )
        legacy_flag = Setting(name="PERMISSIONS_BACKFILL_DONE", value="1")

        user.permission_groups = [legacy_group]
        db.session.add_all([user, legacy_group, legacy_flag])
        db.session.commit()

        sync_permission_data(db.session)
        db.session.expire_all()

        reloaded_user = User.query.filter_by(email="legacygroup@example.com").first()
        assert reloaded_user is not None
        assert reloaded_user.permission_groups == []
        assert PermissionGroup.query.filter_by(key="legacy_full_app_access").first() is None
        assert Setting.query.filter_by(name="PERMISSIONS_BACKFILL_DONE").first() is None


def test_user_without_permission_groups_is_redirected_to_profile_and_blocked_from_restricted_routes(
    client, app
):
    with app.app_context():
        user = User(
            email="restricted@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        user.permission_groups = []
        user.invalidate_permission_cache()
        db.session.commit()

    with client:
        response = login(client, "restricted@example.com", "pass")
        assert response.request.path == "/auth/profile"

        restricted_page = client.get("/purchase_orders")
        assert restricted_page.status_code == 403
        assert b"You do not have permissions to access this page." in restricted_page.data

        profile_page = client.get("/auth/profile")
        assert profile_page.status_code == 200
        assert b"Purchase Orders" not in profile_page.data


def test_permission_group_pages_follow_permissions(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        limited = User(
            email="limited-perms@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(limited)
        db.session.commit()
        limited.permission_groups = []
        limited.invalidate_permission_cache()
        db.session.commit()

        users_group = PermissionGroup(name="User Management")
        db.session.add(users_group)
        db.session.commit()
        limited.permission_groups = [users_group]
        limited.invalidate_permission_cache()
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        admin_response = client.get("/controlpanel/permission-groups")
        assert admin_response.status_code == 200

    with client:
        login(client, "limited-perms@example.com", "pass")
        limited_response = client.get("/controlpanel/permission-groups")
        assert limited_response.status_code == 403


def test_permission_group_create_form_assigns_permissions(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/permission-groups/create",
            data={
                "create-name": "Receiving Team",
                "create-description": "Can work with purchase receiving only.",
                "create-permissions": [
                    "purchase_orders.view",
                    "purchase_invoices.receive",
                ],
                "create-submit": "1",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Permission group created." in response.data

    with app.app_context():
        group = PermissionGroup.query.filter_by(name="Receiving Team").first()
        assert group is not None
        assert {permission.code for permission in group.permissions} == {
            "purchase_orders.view",
            "purchase_invoices.receive",
        }


def test_permission_group_create_rejects_case_insensitive_duplicates(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        existing = PermissionGroup(
            name="Managers",
            description="Existing management group.",
        )
        db.session.add(existing)
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/permission-groups/create",
            data={
                "create-name": "managers",
                "create-description": "Duplicate by case only.",
                "create-submit": "1",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"A permission group with that name already exists." in response.data
    )

    with app.app_context():
        groups = PermissionGroup.query.filter(
            PermissionGroup.name.in_(["Managers", "managers"])
        ).all()
        assert len(groups) == 1


def test_permission_group_create_form_can_copy_permissions_from_existing_groups(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        purchase_view = Permission.query.filter_by(
            code="purchase_orders.view"
        ).first()
        purchase_receive = Permission.query.filter_by(
            code="purchase_invoices.receive"
        ).first()
        transfer_view = Permission.query.filter_by(code="transfers.view").first()

        base_group = PermissionGroup(
            name="Receiving Base",
            description="Starter permissions for receiving.",
        )
        base_group.permissions = [purchase_view, purchase_receive]
        db.session.add(base_group)
        db.session.commit()
        base_group_id = base_group.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/permission-groups/create",
            data={
                "create-name": "Receiving Supervisors",
                "create-description": "Base receiving plus transfer visibility.",
                "create-inherited_group_ids": [str(base_group_id)],
                "create-permissions": ["transfers.view"],
                "create-submit": "1",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Permission group created." in response.data

    with app.app_context():
        group = PermissionGroup.query.filter_by(name="Receiving Supervisors").first()
        assert group is not None
        assert {permission.code for permission in group.permissions} == {
            "purchase_orders.view",
            "purchase_invoices.receive",
            "transfers.view",
        }


def test_permission_group_forms_render_grouped_permission_checkboxes(client):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client.application.app_context():
        list_group = PermissionGroup(
            name="List Actions Group",
            description="Used to verify list actions.",
        )
        db.session.add(list_group)
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)

        list_page = client.get("/controlpanel/permission-groups")
        assert list_page.status_code == 200
        assert b"Create Group" in list_page.data
        assert b'form method="post" class="vstack gap-3" data-permission-group-form' not in list_page.data
        assert b"Delete" in list_page.data

        create_page = client.get("/controlpanel/permission-groups/create")
        assert create_page.status_code == 200
        assert b"Create Permission Group" in create_page.data
        assert b"Copy Permissions From Existing Groups" in create_page.data
        assert b"List Actions Group" in create_page.data
        assert b'data-permission-category-toggle="transfers"' in create_page.data
        assert b"View Transfers" in create_page.data
        assert b"View Dashboard (dashboard.view)" not in create_page.data

        with client.application.app_context():
            group = PermissionGroup(
                name="Transfer Ops",
                description="Transfer-specific permissions.",
            )
            db.session.add(group)
            db.session.commit()
            group_id = group.id

        edit_page = client.get(f"/controlpanel/permission-groups/{group_id}")
        assert edit_page.status_code == 200
        assert b"Edit Permission Group" in edit_page.data
        assert b"Copy Permissions From Existing Groups" in edit_page.data
        assert b'data-permission-category-toggle="purchase_orders"' in edit_page.data
        assert b"Create Purchase Orders" in edit_page.data


def test_permission_group_edit_recreates_missing_permission_rows_on_save(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        group = PermissionGroup(
            name="Scheduling and Communications",
            description="Used to verify missing permissions are recreated.",
        )
        db.session.add(group)
        db.session.commit()
        group_id = group.id

        Permission.query.filter(
            Permission.code.in_(["schedules.view_team", "communications.view_history"])
        ).delete(synchronize_session=False)
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/controlpanel/permission-groups/{group_id}",
            data={
                "group-name": "Scheduling and Communications",
                "group-description": "Recovered missing permissions.",
                "group-permissions": [
                    "schedules.view_team",
                    "communications.view_history",
                ],
                "group-submit": "1",
            },
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Permission group updated." in response.data

    with app.app_context():
        group = db.session.get(PermissionGroup, group_id)
        assert group is not None
        assert {permission.code for permission in group.permissions} == {
            "communications.view_history",
            "schedules.view_team",
        }
        assert Permission.query.filter_by(code="schedules.view_team").first() is not None
        assert (
            Permission.query.filter_by(code="communications.view_history").first()
            is not None
        )


def test_permissions_manager_can_open_permission_group_editor(client, app):
    with app.app_context():
        permissions_manage = Permission.query.filter_by(
            code="permissions.manage"
        ).first()
        permissions_view = Permission.query.filter_by(code="permissions.view").first()

        editor_group = PermissionGroup(name="Permission Editors")
        editor_group.permissions = [permissions_manage, permissions_view]

        target_group = PermissionGroup(
            name="Warehouse Access",
            description="Warehouse-specific permissions.",
        )

        editor = User(
            email="permissions-editor@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        editor.permission_groups = [editor_group]

        db.session.add_all([editor_group, target_group, editor])
        db.session.commit()
        target_group_id = target_group.id

    with client:
        login(client, "permissions-editor@example.com", "pass")
        response = client.get(f"/controlpanel/permission-groups/{target_group_id}")

    assert response.status_code == 200
    assert b"Save Changes" in response.data
    assert b"You can review the group details here" in response.data


def test_import_page_hides_upload_actions_for_view_only_users(client, app):
    with app.app_context():
        user = User(
            email="imports-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "imports.view",
            group_name="Imports View Only",
            description="Can open the imports page but not run uploads.",
        )

    with client:
        login(client, "imports-viewer@example.com", "pass")
        response = client.get("/controlpanel/imports", follow_redirects=True)

    assert response.status_code == 200
    assert b"Download Example" in response.data
    assert (
        b"import uploads are hidden because you do not have permission to run imports."
        in response.data
    )
    assert b'btn btn-primary">Import Locations<' not in response.data
    assert b'type="file"' not in response.data


def test_terminal_sales_mappings_page_hides_delete_actions_for_view_only_users(
    client, app
):
    with app.app_context():
        product = Product(name="Alias Product", price=5.0, cost=1.0)
        location = Location(name="Alias Location")
        user = User(
            email="terminal-mapping-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([product, location, user])
        db.session.flush()
        db.session.add_all(
            [
                TerminalSaleProductAlias(
                    source_name="Terminal Product",
                    normalized_name="terminal_product_view_only",
                    product_id=product.id,
                ),
                TerminalSaleLocationAlias(
                    source_name="Terminal Location",
                    normalized_name="terminal_location_view_only",
                    location_id=location.id,
                ),
            ]
        )
        db.session.commit()
        grant_permissions(
            user,
            "terminal_sales_mappings.view",
            group_name="Terminal Mappings View Only",
            description="Can review terminal sales mappings without deleting them.",
        )

    with client:
        login(client, "terminal-mapping-viewer@example.com", "pass")
        response = client.get(
            "/controlpanel/terminal-sales-mappings", follow_redirects=True
        )

    assert response.status_code == 200
    assert b"You have view-only access to terminal sales mappings." in response.data
    assert b"Terminal Product" in response.data
    assert b"Terminal Location" in response.data
    assert b"Delete Selected" not in response.data
    assert b"Delete All" not in response.data


def test_vendor_item_aliases_page_hides_manage_actions_for_view_only_users(
    client, app
):
    with app.app_context():
        vendor = Vendor(first_name="Vendor", last_name="Viewer")
        item = Item(name="Alias Item", base_unit="each", quantity=1.0)
        user = User(
            email="vendor-alias-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([vendor, item, user])
        db.session.flush()
        db.session.add(
            VendorItemAlias(
                vendor_id=vendor.id,
                item_id=item.id,
                vendor_sku="SKU-1",
                vendor_description="Vendor alias description",
                normalized_description="vendor alias description",
                pack_size="6x1",
                default_cost=2.5,
            )
        )
        db.session.commit()
        grant_permissions(
            user,
            "vendor_item_aliases.view",
            group_name="Vendor Alias View Only",
            description="Can review vendor aliases without editing them.",
        )

    with client:
        login(client, "vendor-alias-viewer@example.com", "pass")
        response = client.get(
            "/controlpanel/vendor-item-aliases", follow_redirects=True
        )

    assert response.status_code == 200
    assert b"You have view-only access to vendor item aliases." in response.data
    assert b"Vendor alias description" in response.data
    assert b"Add Alias" not in response.data
    assert b"Update Alias" not in response.data
    assert b"Delete this alias?" not in response.data
    assert b">View<" in response.data
