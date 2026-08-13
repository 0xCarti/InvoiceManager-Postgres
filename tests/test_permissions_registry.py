from types import SimpleNamespace

from flask import Flask

from app.permissions import (
    AUTHENTICATED_BASELINE_ENDPOINTS,
    PUBLIC_ENDPOINTS,
    get_default_landing_endpoint,
    get_permission_categories,
    get_permission_requirement,
    user_can_access_endpoint,
)
from tests.utils import login


class DummyUser:
    def __init__(self, *permissions, is_super_admin=False, is_authenticated=True):
        self._permissions = set(permissions)
        self.is_super_admin = is_super_admin
        self.is_authenticated = is_authenticated

    def has_permission(self, code: str) -> bool:
        return code in self._permissions


def test_user_can_access_endpoint_requires_matching_permission():
    user = DummyUser("purchase_orders.view")

    assert user_can_access_endpoint(user, "purchase.view_purchase_orders")
    assert not user_can_access_endpoint(user, "purchase.create_purchase_order")
    assert not user_can_access_endpoint(user, "main.metabase_redirect")
    assert user_can_access_endpoint(
        DummyUser("reports.metabase"), "main.metabase_redirect"
    )
    assert not user_can_access_endpoint(
        DummyUser("dashboard.view"), "main.add_metabase_card", "POST"
    )
    assert user_can_access_endpoint(
        DummyUser(
            "dashboard.view",
            "dashboard.view_cards",
            "dashboard.manage_cards",
        ),
        "main.add_metabase_card",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser(
            "dashboard.view",
            "dashboard.view_cards",
            "dashboard.manage_cards",
        ),
        "main.update_metabase_card_settings",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("communications.view_bulletin_receipts"),
        "communication.center",
    )
    assert not user_can_access_endpoint(
        DummyUser("communications.global_scope"),
        "communication.center",
    )
    assert user_can_access_endpoint(
        DummyUser("communications.view"),
        "communication.bulletin_detail",
    )
    assert user_can_access_endpoint(
        DummyUser("communications.view"),
        "communication.messages",
    )
    assert user_can_access_endpoint(
        DummyUser("communications.view"),
        "communication.message_detail",
    )
    assert not user_can_access_endpoint(
        DummyUser("events.reports"),
        "event.email_bulk_stand_sheets",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("events.email_stand_sheets"),
        "event.email_bulk_stand_sheets",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("events.reports"),
        "event.inventory_comparison_report",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("events.reports"),
        "event.bulk_count_sheets_csv",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("events.reports"),
        "event.inventory_report_csv",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("events.reports"),
        "event.inventory_comparison_report_csv",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("events.view"),
        "event.download_event_document",
    )
    assert not user_can_access_endpoint(
        DummyUser("events.view"),
        "event.upload_event_document",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("events.edit"),
        "event.upload_event_document",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("events.view"),
        "event.delete_event_document_file",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("events.edit"),
        "event.delete_event_document_file",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("purchase_orders.view"),
        "purchase.mark_purchase_order_ordered",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.view_equipment",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.view_equipment_intake",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.view_equipment_maintenance",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.view_equipment_maintenance_issue",
    )
    assert not user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.create_equipment_asset",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.create"),
        "equipment.create_equipment_asset",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("equipment.print_labels"),
        "equipment.print_equipment_labels",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.print_labels"),
        "equipment.print_equipment_labels",
    )
    assert not user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.view_equipment_asset_scan",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.manage_custody"),
        "equipment.view_equipment_asset_scan",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.manage_custody"),
        "equipment.check_out_equipment_asset",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.manage_custody"),
        "equipment.check_in_equipment_asset",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.create_equipment_intake_batch",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.manage_intake"),
        "equipment.create_equipment_intake_batch",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.manage_intake"),
        "equipment.receive_equipment_intake_batch",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.import_equipment_from_snipe_it",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.import"),
        "equipment.import_equipment_from_snipe_it",
    )
    assert not user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.view_equipment_catalog",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.manage_models"),
        "equipment.view_equipment_catalog",
    )
    assert not user_can_access_endpoint(
        DummyUser("equipment.view"),
        "equipment.create_equipment_maintenance_issue",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.manage_maintenance"),
        "equipment.create_equipment_maintenance_issue",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("equipment.view", "equipment.manage_maintenance"),
        "equipment.add_equipment_maintenance_update",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("purchase_orders.view", "purchase_orders.mark_ordered"),
        "purchase.mark_purchase_order_ordered",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("purchase_orders.view", "purchase_orders.edit"),
        "purchase.mark_purchase_order_ordered",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("items.view"),
        "item.selected_item_rows",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("items.view"),
        "item.item_units",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("transfers.create"),
        "item.item_units",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("purchase_orders.resolve_vendor_items"),
        "item.item_units",
        "GET",
    )
    assert not user_can_access_endpoint(
        DummyUser("transfers.create"),
        "item.item_units",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("items.manage_units"),
        "item.item_units",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("items.view", "items.delete"),
        "item.duplicate_items",
        "GET",
    )
    assert not user_can_access_endpoint(
        DummyUser("items.view"),
        "item.duplicate_items",
        "GET",
    )
    assert not user_can_access_endpoint(
        DummyUser("items.delete"),
        "item.duplicate_items",
        "GET",
    )
    assert user_can_access_endpoint(
        DummyUser("reports.equipment_procurement"),
        "report.equipment_procurement_report",
    )
    assert user_can_access_endpoint(
        DummyUser("schedules.manage_templates"),
        "schedule.templates",
    )
    assert user_can_access_endpoint(
        DummyUser("schedules.apply_templates"),
        "schedule.templates",
    )
    assert user_can_access_endpoint(
        DummyUser("schedules.view_self"),
        "schedule.my_schedule",
    )
    assert user_can_access_endpoint(
        DummyUser("schedules.post_tradeboard"),
        "schedule.my_schedule",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("schedules.post_tradeboard"),
        "schedule.my_schedule",
        "GET",
    )
    assert not user_can_access_endpoint(
        DummyUser("schedules.view_self"),
        "schedule.team_schedule",
    )
    assert not user_can_access_endpoint(
        DummyUser("schedules.apply_templates"),
        "schedule.template_detail",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("schedules.manage_templates"),
        "schedule.template_detail",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("signage.view"),
        "signage.view_signage_media_assets",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("signage.manage_media"),
        "signage.view_signage_media_assets",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("signage.manage_displays"),
        "signage.add_board_template",
        "POST",
    )
    assert user_can_access_endpoint(
        DummyUser("signage.manage_board_templates"),
        "signage.add_board_template",
        "POST",
    )
    assert not user_can_access_endpoint(
        DummyUser("locations.view"),
        "locations.count_submissions",
    )
    assert user_can_access_endpoint(
        DummyUser("events.manage_locations"),
        "locations.count_submissions",
    )
    assert not user_can_access_endpoint(
        DummyUser("locations.view"),
        "locations.print_count_sign",
    )
    assert user_can_access_endpoint(
        DummyUser("locations.edit"),
        "locations.print_count_sign",
    )
    assert not user_can_access_endpoint(
        DummyUser("locations.view"),
        "locations.print_transfer_sign",
    )
    assert user_can_access_endpoint(
        DummyUser("locations.edit"),
        "locations.print_transfer_sign",
    )


def test_super_admin_bypasses_endpoint_permission_checks():
    user = DummyUser(is_super_admin=True)

    assert user_can_access_endpoint(user, "admin.settings")
    assert user_can_access_endpoint(user, "admin.download_sales_import_attachment")
    assert user_can_access_endpoint(user, "admin.sales_import_detail", "POST")


def test_unknown_endpoints_fail_closed_even_for_super_admin():
    assert not user_can_access_endpoint(DummyUser(), "unregistered.endpoint")
    assert not user_can_access_endpoint(
        DummyUser(is_super_admin=True),
        "unregistered.endpoint",
    )


def test_public_and_authenticated_baseline_endpoints_are_explicit():
    anonymous = DummyUser(is_authenticated=False)
    authenticated = DummyUser()

    assert user_can_access_endpoint(anonymous, "auth.login")
    assert not user_can_access_endpoint(anonymous, "auth.profile")
    assert user_can_access_endpoint(authenticated, "auth.profile")


def test_head_uses_get_permission_requirement():
    user = DummyUser("items.view")

    assert get_permission_requirement("item.view_items", "HEAD") == (
        get_permission_requirement("item.view_items", "GET")
    )
    assert user_can_access_endpoint(user, "item.view_items", "HEAD")


def test_event_sheet_mutations_require_manage_sales_permission():
    report_user = DummyUser("events.reports")
    count_manager = DummyUser("events.manage_sales")

    assert user_can_access_endpoint(report_user, "event.stand_sheet", "GET")
    assert not user_can_access_endpoint(
        report_user, "event.stand_sheet", "POST"
    )
    assert user_can_access_endpoint(count_manager, "event.stand_sheet", "POST")
    assert user_can_access_endpoint(report_user, "event.count_sheet", "GET")
    assert not user_can_access_endpoint(
        report_user, "event.count_sheet", "POST"
    )
    assert user_can_access_endpoint(count_manager, "event.count_sheet", "POST")


def test_monthly_team_schedule_matches_weekly_view_access():
    allowed_permissions = (
        "schedules.view_team",
        "schedules.edit_team",
        "schedules.publish",
        "schedules.view_labor",
        "schedules.view_seen_status",
        "schedules.self_schedule",
    )

    for permission in allowed_permissions:
        user = DummyUser(permission)
        assert user_can_access_endpoint(user, "schedule.team_schedule", "GET")
        assert user_can_access_endpoint(
            user,
            "schedule.team_schedule_month",
            "GET",
        )

    assert not user_can_access_endpoint(
        DummyUser("schedules.view_self"),
        "schedule.team_schedule_month",
        "GET",
    )
    assert not user_can_access_endpoint(
        DummyUser("schedules.view_team"),
        "schedule.team_schedule_month",
        "POST",
    )


def test_products_sold_report_requires_its_dedicated_permission():
    assert not user_can_access_endpoint(
        DummyUser("reports.product_sales"),
        "report.products_sold_report",
    )
    assert not user_can_access_endpoint(
        DummyUser("reports.product_stock_usage"),
        "report.products_sold_report",
    )
    assert user_can_access_endpoint(
        DummyUser("reports.products_sold"),
        "report.products_sold_report",
    )


def test_invoice_gl_permission_reaches_report_hub():
    assert user_can_access_endpoint(
        DummyUser("reports.invoice_gl_codes"),
        "report.index",
    )


def test_default_landing_endpoint_prefers_first_accessible_route():
    app = Flask(__name__)
    app.view_functions.update(
        {
            "transfer.view_transfers": SimpleNamespace(),
            "main.home": SimpleNamespace(),
            "admin.users": SimpleNamespace(),
            "schedule.my_schedule": SimpleNamespace(),
            "auth.profile": SimpleNamespace(),
        }
    )

    with app.app_context():
        assert get_default_landing_endpoint(DummyUser("transfers.view")) == (
            "transfer.view_transfers"
        )
        assert get_default_landing_endpoint(DummyUser("dashboard.view")) == "main.home"
        assert get_default_landing_endpoint(DummyUser("users.view")) == "admin.users"
        assert get_default_landing_endpoint(DummyUser("schedules.view_self")) == (
            "schedule.my_schedule"
        )
        assert get_default_landing_endpoint(DummyUser()) == "auth.profile"


def test_permission_categories_include_system_admin_section():
    categories = get_permission_categories()
    labels = {category["label"] for category in categories}

    assert "Transfers" in labels
    assert "Permission Groups" in labels
    assert "Permissions" in labels


def test_all_non_public_registered_endpoints_have_permission_rules(app):
    explicitly_classified = PUBLIC_ENDPOINTS | AUTHENTICATED_BASELINE_ENDPOINTS
    registered_endpoints = {rule.endpoint for rule in app.url_map.iter_rules()}

    assert PUBLIC_ENDPOINTS.isdisjoint(AUTHENTICATED_BASELINE_ENDPOINTS)
    assert explicitly_classified <= registered_endpoints

    missing = set()
    for rule in app.url_map.iter_rules():
        endpoint = rule.endpoint
        if endpoint in explicitly_classified:
            continue
        for method in rule.methods - {"OPTIONS"}:
            if get_permission_requirement(endpoint, method) is None:
                missing.add((endpoint, method))

    assert sorted(missing) == []


def test_runtime_hook_denies_unregistered_route_for_anonymous_and_admin(
    client, app
):
    endpoint = "permission_test_unregistered"

    def unregistered_view():
        return "unexpected"

    app.add_url_rule(
        "/_permission-test/unregistered",
        endpoint=endpoint,
        view_func=unregistered_view,
        methods=["GET"],
    )

    anonymous_response = client.get("/_permission-test/unregistered")
    assert anonymous_response.status_code == 302
    assert "/auth/login" in anonymous_response.headers["Location"]

    login(client, "admin@example.com", "adminpass")
    admin_response = client.get("/_permission-test/unregistered")
    assert admin_response.status_code == 403
