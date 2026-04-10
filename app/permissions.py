from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from flask import current_app


@dataclass(frozen=True)
class PermissionDefinition:
    code: str
    category: str
    label: str
    description: str


@dataclass(frozen=True)
class PermissionRequirement:
    any_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()


def _perm(code: str, category: str, label: str, description: str) -> PermissionDefinition:
    return PermissionDefinition(
        code=code,
        category=category,
        label=label,
        description=description,
    )


def requirement(
    *, any_of: Iterable[str] = (), all_of: Iterable[str] = ()
) -> PermissionRequirement:
    return PermissionRequirement(tuple(any_of), tuple(all_of))


PERMISSION_CATEGORY_LABELS: dict[str, str] = {
    "dashboard": "Dashboard",
    "transfers": "Transfers",
    "items": "Items",
    "locations": "Locations",
    "menus": "Menus",
    "products": "Products",
    "spoilage": "Spoilage",
    "gl_codes": "GL Codes",
    "purchase_orders": "Purchase Orders",
    "purchase_invoices": "Purchase Invoices",
    "customers": "Customers",
    "vendors": "Vendors",
    "invoices": "Sales Invoices",
    "events": "Events",
    "reports": "Reports",
    "schedules": "Schedules",
    "communications": "Communications",
    "users": "Users",
    "permission_groups": "Permission Groups",
    "permissions": "Permissions",
    "backups": "Backups",
    "settings": "Settings",
    "imports": "Imports",
    "activity_logs": "Activity Logs",
    "system_info": "System Info",
    "terminal_sales_mappings": "Terminal Sales Mappings",
    "sales_imports": "Sales Import Review",
    "vendor_item_aliases": "Vendor Item Aliases",
}


PERMISSION_DEFINITIONS: tuple[PermissionDefinition, ...] = (
    _perm("dashboard.view", "dashboard", "View Dashboard", "View the main dashboard."),
    _perm("transfers.view", "transfers", "View Transfers", "View transfers and transfer details."),
    _perm("transfers.create", "transfers", "Create Transfers", "Create new transfers."),
    _perm("transfers.edit", "transfers", "Edit Transfers", "Edit existing transfers."),
    _perm("transfers.delete", "transfers", "Delete Transfers", "Delete transfers."),
    _perm("transfers.complete", "transfers", "Approve Transfers", "Complete or uncomplete transfers."),
    _perm("transfers.report", "transfers", "Transfer Reports", "View transfer reports and exports."),
    _perm("items.view", "items", "View Items", "View item lists, details, and item history."),
    _perm("items.create", "items", "Create Items", "Create items and quick-add items."),
    _perm("items.edit", "items", "Edit Items", "Edit item details."),
    _perm("items.delete", "items", "Delete Items", "Archive or bulk-delete items."),
    _perm("items.bulk_update", "items", "Bulk Update Items", "Use item bulk-update tools."),
    _perm("items.import", "items", "Import Items", "Import items from files."),
    _perm("items.manage_units", "items", "Manage Item Units", "Manage alternate item units."),
    _perm("locations.view", "locations", "View Locations", "View locations and stand sheets."),
    _perm("locations.create", "locations", "Create Locations", "Create locations."),
    _perm("locations.edit", "locations", "Edit Locations", "Edit locations."),
    _perm("locations.delete", "locations", "Delete Locations", "Archive locations."),
    _perm("locations.bulk_update", "locations", "Bulk Update Locations", "Use location bulk-update tools."),
    _perm("locations.manage_items", "locations", "Manage Location Items", "Manage the item lists assigned to locations."),
    _perm("locations.email_stand_sheets", "locations", "Email Stand Sheets", "Email stand sheets from locations."),
    _perm("menus.view", "menus", "View Menus", "View menus."),
    _perm("menus.create", "menus", "Create Menus", "Create menus."),
    _perm("menus.edit", "menus", "Edit Menus", "Edit menus."),
    _perm("menus.delete", "menus", "Delete Menus", "Delete menus."),
    _perm("menus.assign", "menus", "Assign Menus", "Assign menus to locations."),
    _perm("products.view", "products", "View Products", "View products and product lists."),
    _perm("products.create", "products", "Create Products", "Create products."),
    _perm("products.edit", "products", "Edit Products", "Edit products."),
    _perm("products.delete", "products", "Delete Products", "Delete products."),
    _perm("products.bulk_update", "products", "Bulk Update Products", "Use product bulk-update tools."),
    _perm("products.manage_recipe", "products", "Manage Product Recipes", "Edit product recipes and recipe cost tools."),
    _perm("products.manage_aliases", "products", "Manage Product POS Aliases", "Manage terminal-sale product aliases."),
    _perm("spoilage.view", "spoilage", "View Spoilage", "View spoilage reports."),
    _perm("gl_codes.view", "gl_codes", "View GL Codes", "View GL codes."),
    _perm("gl_codes.create", "gl_codes", "Create GL Codes", "Create GL codes."),
    _perm("gl_codes.edit", "gl_codes", "Edit GL Codes", "Edit GL codes."),
    _perm("gl_codes.delete", "gl_codes", "Delete GL Codes", "Delete GL codes."),
    _perm("purchase_orders.view", "purchase_orders", "View Purchase Orders", "View purchase orders."),
    _perm("purchase_orders.create", "purchase_orders", "Create Purchase Orders", "Create purchase orders."),
    _perm("purchase_orders.edit", "purchase_orders", "Edit Purchase Orders", "Edit purchase orders."),
    _perm("purchase_orders.delete", "purchase_orders", "Delete Purchase Orders", "Delete purchase orders."),
    _perm("purchase_orders.merge", "purchase_orders", "Merge Purchase Orders", "Merge purchase orders."),
    _perm("purchase_orders.upload", "purchase_orders", "Upload Purchase Orders", "Upload purchase orders from files."),
    _perm("purchase_orders.resolve_vendor_items", "purchase_orders", "Resolve Vendor Items", "Resolve vendor item mappings for uploaded purchase orders."),
    _perm("purchase_orders.recommendations", "purchase_orders", "Purchase Recommendations", "Use purchase order recommendation tools."),
    _perm("purchase_invoices.view", "purchase_invoices", "View Purchase Invoices", "View purchase invoices."),
    _perm("purchase_invoices.receive", "purchase_invoices", "Receive Purchase Invoices", "Receive purchase orders into inventory."),
    _perm("purchase_invoices.reverse", "purchase_invoices", "Reverse Purchase Invoices", "Reverse received purchase invoices."),
    _perm("customers.view", "customers", "View Customers", "View customers."),
    _perm("customers.create", "customers", "Create Customers", "Create customers."),
    _perm("customers.edit", "customers", "Edit Customers", "Edit customers."),
    _perm("customers.delete", "customers", "Delete Customers", "Archive customers."),
    _perm("vendors.view", "vendors", "View Vendors", "View vendors."),
    _perm("vendors.create", "vendors", "Create Vendors", "Create vendors."),
    _perm("vendors.edit", "vendors", "Edit Vendors", "Edit vendors."),
    _perm("vendors.delete", "vendors", "Delete Vendors", "Archive vendors."),
    _perm("invoices.view", "invoices", "View Sales Invoices", "View sales invoices."),
    _perm("invoices.create", "invoices", "Create Sales Invoices", "Create sales invoices."),
    _perm("invoices.delete", "invoices", "Delete Sales Invoices", "Delete sales invoices."),
    _perm("invoices.manage_payment", "invoices", "Manage Invoice Status", "Mark invoices delivered and paid."),
    _perm("events.view", "events", "View Events", "View events and event detail pages."),
    _perm("events.create", "events", "Create Events", "Create events."),
    _perm("events.edit", "events", "Edit Events", "Edit events."),
    _perm("events.delete", "events", "Delete Events", "Delete events."),
    _perm("events.manage_locations", "events", "Manage Event Locations", "Manage event locations and opening counts."),
    _perm("events.manage_sales", "events", "Manage Event Sales", "Add manual sales, scan counts, and upload terminal sales."),
    _perm("events.confirm_locations", "events", "Confirm Event Locations", "Confirm and unconfirm event locations."),
    _perm("events.close", "events", "Close Events", "Close events."),
    _perm("events.reports", "events", "Event Reports", "View event reports, sheets, and exports."),
    _perm("reports.customer_invoices", "reports", "Customer Invoice Report", "Run the customer invoice report."),
    _perm("reports.received_invoices", "reports", "Received Invoice Report", "Run the received invoice report."),
    _perm("reports.purchase_inventory_summary", "reports", "Purchase Inventory Summary", "Run the purchase inventory summary report."),
    _perm("reports.inventory_variance", "reports", "Inventory Variance Report", "Run the inventory variance report."),
    _perm("reports.invoice_gl_codes", "reports", "Purchase Invoice GL Report", "View purchase invoice GL code reports."),
    _perm("reports.product_sales", "reports", "Revenue Report", "Run the revenue report."),
    _perm("reports.product_stock_usage", "reports", "Stock Usage Report", "Run the stock usage report."),
    _perm("reports.product_recipe", "reports", "Recipe Report", "Run the product recipe report."),
    _perm("reports.product_location_sales", "reports", "Product Location Sales Report", "Run the product location sales report."),
    _perm("reports.event_terminal_sales", "reports", "Event Terminal Sales Report", "Run the event terminal sales report."),
    _perm("reports.purchase_cost_forecast", "reports", "Forecasted Stock Item Sales", "Run the purchase cost forecast report."),
    _perm("reports.department_sales_forecast", "reports", "Department Sales Forecast", "Run the department sales forecast workflow."),
    _perm("reports.metabase", "reports", "Access Metabase", "Open the Metabase analytics workspace."),
    _perm("schedules.view_team", "schedules", "View Team Schedule", "View team schedule boards for managed departments."),
    _perm("schedules.view_self", "schedules", "View My Schedule", "View your own published schedule."),
    _perm("schedules.edit_team", "schedules", "Edit Team Schedule", "Create and edit team schedule shifts."),
    _perm("schedules.self_schedule", "schedules", "Schedule Self", "Create and edit your own shifts on the schedule board."),
    _perm("schedules.delete", "schedules", "Delete Schedule Shifts", "Delete shifts from draft schedules."),
    _perm("schedules.publish", "schedules", "Publish Schedule", "Publish and unpublish department schedule weeks."),
    _perm("schedules.view_labor", "schedules", "View Labor Forecast", "View scheduled labor totals and labor forecast summaries."),
    _perm("schedules.manage_pay_rates", "schedules", "Manage Pay Rates", "Manage scheduling pay-rate and hours targets."),
    _perm("schedules.manage_setup", "schedules", "Manage Scheduling Setup", "Manage departments, positions, memberships, and scheduling structure."),
    _perm("schedules.manage_self_availability", "schedules", "Manage My Availability", "Manage your recurring availability and overrides."),
    _perm("schedules.manage_team_availability", "schedules", "Manage Team Availability", "Manage availability settings for scoped users."),
    _perm("schedules.request_time_off", "schedules", "Request Time Off", "Submit and cancel your own time-off requests."),
    _perm("schedules.view_self_time_off", "schedules", "View My Time Off", "View your own time-off requests."),
    _perm("schedules.view_team_time_off", "schedules", "View Team Time Off", "View time-off requests for scoped users."),
    _perm("schedules.approve_time_off", "schedules", "Approve Time Off", "Approve or deny time-off requests for scoped users."),
    _perm("schedules.auto_assign", "schedules", "Auto Assign Shifts", "Run the schedule auto-assignment workflow."),
    _perm("schedules.view_seen_status", "schedules", "View Seen Status", "View who has seen published schedule versions."),
    _perm("schedules.view_tradeboard", "schedules", "View Tradeboard", "View open and tradeboard shifts."),
    _perm("schedules.claim_tradeboard", "schedules", "Claim Tradeboard Shifts", "Request tradeboard and open shifts."),
    _perm("schedules.approve_tradeboard", "schedules", "Approve Tradeboard Claims", "Approve or reject tradeboard claim requests."),
    _perm("communications.view", "communications", "View Communications", "View your inbox and the bulletin board."),
    _perm("communications.view_history", "communications", "View Message History", "View scoped messages sent between other users."),
    _perm("communications.send_direct", "communications", "Send Direct Messages", "Send messages to scoped users."),
    _perm("communications.send_broadcast", "communications", "Send Broadcasts", "Broadcast messages to multiple users, departments, or all scoped users."),
    _perm("communications.manage_bulletin", "communications", "Manage Bulletin Board", "Post and archive pinned bulletin board updates."),
    _perm("users.view", "users", "View Users", "View the user list and user access pages."),
    _perm("users.manage", "users", "Manage Users", "Invite users, activate users, archive users, and assign groups."),
    _perm("permission_groups.view", "permission_groups", "View Permission Groups", "View permission groups."),
    _perm("permission_groups.manage", "permission_groups", "Manage Permission Groups", "Create and delete permission groups."),
    _perm("permissions.view", "permissions", "View Permissions", "View the permission catalog."),
    _perm("permissions.manage", "permissions", "Assign Permissions", "Assign permissions to permission groups."),
    _perm("backups.view", "backups", "View Backups", "View available backups."),
    _perm("backups.create", "backups", "Create Backups", "Create database backups."),
    _perm("backups.restore", "backups", "Restore Backups", "Restore database backups."),
    _perm("backups.download", "backups", "Download Backups", "Download backup files."),
    _perm("settings.view", "settings", "View Settings", "View application settings."),
    _perm("settings.manage", "settings", "Manage Settings", "Update application settings."),
    _perm("imports.view", "imports", "View Imports", "View control-panel import tools."),
    _perm("imports.run", "imports", "Run Imports", "Run control-panel imports."),
    _perm("activity_logs.view", "activity_logs", "View Activity Logs", "View activity logs."),
    _perm("system_info.view", "system_info", "View System Info", "View system information."),
    _perm("terminal_sales_mappings.view", "terminal_sales_mappings", "View Terminal Sales Mappings", "View terminal-sales mappings."),
    _perm("terminal_sales_mappings.manage", "terminal_sales_mappings", "Manage Terminal Sales Mappings", "Delete terminal-sales mappings."),
    _perm("sales_imports.view", "sales_imports", "View Sales Import Review", "View sales import review pages."),
    _perm("sales_imports.manage", "sales_imports", "Manage Sales Import Review", "Resolve, approve, reverse, and delete sales imports."),
    _perm("vendor_item_aliases.view", "vendor_item_aliases", "View Vendor Item Aliases", "View vendor item aliases."),
    _perm("vendor_item_aliases.manage", "vendor_item_aliases", "Manage Vendor Item Aliases", "Create, edit, and delete vendor item aliases."),
)


PERMISSION_DEFINITIONS_BY_CODE = {
    definition.code: definition for definition in PERMISSION_DEFINITIONS
}


ENDPOINT_PERMISSION_RULES: dict[str, PermissionRequirement] = {
    "main.home": requirement(any_of=("dashboard.view",)),
    "main.metabase_redirect": requirement(any_of=("reports.metabase",)),
    "transfer.view_transfers": requirement(any_of=("transfers.view",)),
    "transfer.add_transfer": requirement(any_of=("transfers.create",)),
    "transfer.ajax_add_transfer": requirement(any_of=("transfers.create",)),
    "transfer.edit_transfer": requirement(any_of=("transfers.edit",)),
    "transfer.transfer_json": requirement(any_of=("transfers.view",)),
    "transfer.ajax_edit_transfer": requirement(any_of=("transfers.edit",)),
    "transfer.delete_transfer": requirement(any_of=("transfers.delete",)),
    "transfer.complete_transfer": requirement(any_of=("transfers.complete",)),
    "transfer.complete_transfer_item": requirement(any_of=("transfers.complete",)),
    "transfer.uncomplete_transfer": requirement(any_of=("transfers.complete",)),
    "transfer.uncomplete_transfer_item": requirement(any_of=("transfers.complete",)),
    "transfer.view_transfer": requirement(any_of=("transfers.view",)),
    "transfer.generate_report": requirement(any_of=("transfers.report",)),
    "transfer.view_report": requirement(any_of=("transfers.report",)),
    "item.view_items": requirement(any_of=("items.view",)),
    "item.recipe_cost_calculator": requirement(any_of=("items.view",)),
    "item.view_item": requirement(any_of=("items.view",)),
    "item.add_item": requirement(any_of=("items.create",)),
    "item.copy_item": requirement(any_of=("items.create",)),
    "item.edit_item": requirement(any_of=("items.edit",)),
    "item.delete_item": requirement(any_of=("items.delete",)),
    "item.bulk_delete_items": requirement(any_of=("items.delete",)),
    "item.quick_add_item": requirement(any_of=("items.create",)),
    "item.import_items": requirement(any_of=("items.import",)),
    "locations.add_location": requirement(any_of=("locations.create",)),
    "locations.edit_location": requirement(any_of=("locations.edit",)),
    "locations.copy_location_items": requirement(any_of=("locations.manage_items",)),
    "locations.view_stand_sheet": requirement(any_of=("locations.view",)),
    "locations.email_stand_sheets": requirement(any_of=("locations.email_stand_sheets",)),
    "locations.add_location_item": requirement(any_of=("locations.manage_items",)),
    "locations.delete_location_item": requirement(any_of=("locations.manage_items",)),
    "locations.view_locations": requirement(any_of=("locations.view",)),
    "locations.delete_location": requirement(any_of=("locations.delete",)),
    "menu.view_menus": requirement(any_of=("menus.view",)),
    "menu.add_menu": requirement(any_of=("menus.create",)),
    "menu.edit_menu": requirement(any_of=("menus.edit",)),
    "menu.delete_menu": requirement(any_of=("menus.delete",)),
    "menu.assign_menu": requirement(any_of=("menus.assign",)),
    "menu.get_menu_products": requirement(any_of=("menus.view", "menus.assign")),
    "customer.view_customers": requirement(any_of=("customers.view",)),
    "customer.create_customer": requirement(any_of=("customers.create",)),
    "customer.edit_customer": requirement(any_of=("customers.edit",)),
    "customer.create_customer_modal": requirement(any_of=("customers.create",)),
    "customer.delete_customer": requirement(any_of=("customers.delete",)),
    "vendor.view_vendors": requirement(any_of=("vendors.view",)),
    "vendor.create_vendor": requirement(any_of=("vendors.create",)),
    "vendor.edit_vendor": requirement(any_of=("vendors.edit",)),
    "vendor.delete_vendor": requirement(any_of=("vendors.delete",)),
    "product.view_products": requirement(any_of=("products.view",)),
    "product.create_product": requirement(any_of=("products.create",)),
    "product.ajax_create_product": requirement(any_of=("products.create",)),
    "product.quick_create_product": requirement(any_of=("products.create",)),
    "product.copy_product": requirement(any_of=("products.create",)),
    "product.edit_product": requirement(any_of=("products.edit",)),
    "product.remove_terminal_sale_alias": requirement(any_of=("products.manage_aliases",)),
    "product.edit_product_recipe": requirement(any_of=("products.manage_recipe",)),
    "product.calculate_product_cost": requirement(any_of=("products.view", "products.manage_recipe")),
    "product.calculate_product_cost_preview": requirement(any_of=("products.create", "products.edit", "products.manage_recipe")),
    "product.bulk_set_cost_from_recipe": requirement(any_of=("products.bulk_update",)),
    "product.delete_product": requirement(any_of=("products.delete",)),
    "invoice.create_invoice": requirement(any_of=("invoices.create",)),
    "invoice.delete_invoice": requirement(any_of=("invoices.delete",)),
    "invoice.mark_invoice_delivered": requirement(any_of=("invoices.manage_payment",)),
    "invoice.mark_invoice_paid": requirement(any_of=("invoices.manage_payment",)),
    "invoice.mark_invoice_unpaid": requirement(any_of=("invoices.manage_payment",)),
    "invoice.bulk_invoice_payment_status": requirement(any_of=("invoices.manage_payment",)),
    "invoice.view_invoice": requirement(any_of=("invoices.view",)),
    "invoice.get_customer_tax_status": requirement(any_of=("invoices.create",)),
    "invoice.filter_invoices_api": requirement(any_of=("invoices.view",)),
    "invoice.create_invoice_api": requirement(any_of=("invoices.create",)),
    "invoice.view_invoices": requirement(any_of=("invoices.view",)),
    "purchase.view_purchase_orders": requirement(any_of=("purchase_orders.view",)),
    "purchase.merge_purchase_orders_route": requirement(any_of=("purchase_orders.merge",)),
    "purchase.upload_purchase_order": requirement(any_of=("purchase_orders.upload",)),
    "purchase.resolve_vendor_items": requirement(any_of=("purchase_orders.resolve_vendor_items",)),
    "purchase.create_purchase_order": requirement(any_of=("purchase_orders.create",)),
    "purchase.purchase_order_recommendations": requirement(any_of=("purchase_orders.recommendations",)),
    "purchase.edit_purchase_order": requirement(any_of=("purchase_orders.edit",)),
    "purchase.delete_purchase_order": requirement(any_of=("purchase_orders.delete",)),
    "purchase.receive_invoice": requirement(any_of=("purchase_invoices.receive",)),
    "purchase.view_purchase_invoices": requirement(any_of=("purchase_invoices.view",)),
    "purchase.view_purchase_invoice": requirement(any_of=("purchase_invoices.view",)),
    "purchase.legacy_purchase_invoice_report": requirement(any_of=("reports.invoice_gl_codes",)),
    "purchase.reverse_purchase_invoice": requirement(any_of=("purchase_invoices.reverse",)),
    "report.department_sales_forecast": requirement(any_of=("reports.department_sales_forecast",)),
    "report.customer_invoice_report": requirement(any_of=("reports.customer_invoices",)),
    "report.customer_invoice_report_results": requirement(any_of=("reports.customer_invoices",)),
    "report.received_invoice_report": requirement(any_of=("reports.received_invoices",)),
    "report.purchase_inventory_summary": requirement(any_of=("reports.purchase_inventory_summary",)),
    "report.inventory_variance_report": requirement(any_of=("reports.inventory_variance",)),
    "report.invoice_gl_code_report": requirement(any_of=("reports.invoice_gl_codes",)),
    "report.product_sales_report": requirement(any_of=("reports.product_sales",)),
    "report.product_stock_usage_report": requirement(any_of=("reports.product_stock_usage",)),
    "report.product_recipe_report": requirement(any_of=("reports.product_recipe",)),
    "report.product_location_sales_report": requirement(any_of=("reports.product_location_sales",)),
    "report.event_terminal_sales_report": requirement(any_of=("reports.event_terminal_sales",)),
    "report.purchase_cost_forecast": requirement(any_of=("reports.purchase_cost_forecast",)),
    "event.view_events": requirement(any_of=("events.view",)),
    "event.create_event": requirement(any_of=("events.create",)),
    "event.filter_events_ajax": requirement(any_of=("events.view",)),
    "event.create_event_ajax": requirement(any_of=("events.create",)),
    "event.edit_event": requirement(any_of=("events.edit",)),
    "event.delete_event": requirement(any_of=("events.delete",)),
    "event.view_event": requirement(any_of=("events.view",)),
    "event.closed_event_report": requirement(any_of=("events.reports",)),
    "event.update_opening_counts": requirement(any_of=("events.manage_locations",)),
    "event.add_location": requirement(any_of=("events.manage_locations",)),
    "event.add_terminal_sale": requirement(any_of=("events.manage_sales",)),
    "event.scan_counts": requirement(any_of=("events.manage_sales",)),
    "event.upload_terminal_sales": requirement(any_of=("events.manage_sales",)),
    "event.confirm_location": requirement(any_of=("events.confirm_locations",)),
    "event.undo_confirm_location": requirement(any_of=("events.confirm_locations",)),
    "event.stand_sheet": requirement(any_of=("events.reports",)),
    "event.sustainability_dashboard": requirement(any_of=("events.reports",)),
    "event.sustainability_dashboard_print": requirement(any_of=("events.reports",)),
    "event.sustainability_dashboard_csv": requirement(any_of=("events.reports",)),
    "event.count_sheet": requirement(any_of=("events.reports",)),
    "event.bulk_stand_sheets": requirement(any_of=("events.reports",)),
    "event.email_bulk_stand_sheets": requirement(any_of=("events.reports",)),
    "event.bulk_count_sheets": requirement(any_of=("events.reports",)),
    "event.close_event": requirement(any_of=("events.close",)),
    "event.inventory_report": requirement(any_of=("events.reports",)),
    "schedule.team_schedule": requirement(
        any_of=(
            "schedules.view_team",
            "schedules.edit_team",
            "schedules.publish",
            "schedules.view_labor",
            "schedules.view_seen_status",
            "schedules.self_schedule",
        )
    ),
    "schedule.my_schedule": requirement(
        any_of=("schedules.view_self", "schedules.self_schedule")
    ),
    "schedule.availability": requirement(
        any_of=(
            "schedules.manage_self_availability",
            "schedules.manage_team_availability",
        )
    ),
    "schedule.time_off": requirement(
        any_of=(
            "schedules.request_time_off",
            "schedules.view_self_time_off",
            "schedules.view_team_time_off",
            "schedules.approve_time_off",
        )
    ),
    "schedule.tradeboard": requirement(
        any_of=(
            "schedules.view_tradeboard",
            "schedules.claim_tradeboard",
            "schedules.approve_tradeboard",
        )
    ),
    "schedule.setup": requirement(
        any_of=("schedules.manage_setup", "schedules.manage_pay_rates")
    ),
    "schedule.user_settings": requirement(
        any_of=("schedules.manage_setup", "schedules.manage_pay_rates")
    ),
    "communication.center": requirement(
        any_of=(
            "communications.view",
            "communications.view_history",
            "communications.send_direct",
            "communications.send_broadcast",
            "communications.manage_bulletin",
        )
    ),
    "glcode.view_gl_codes": requirement(any_of=("gl_codes.view",)),
    "glcode.create_gl_code": requirement(any_of=("gl_codes.create",)),
    "glcode.edit_gl_code": requirement(any_of=("gl_codes.edit",)),
    "glcode.delete_gl_code": requirement(any_of=("gl_codes.delete",)),
    "glcode.ajax_create_gl_code": requirement(any_of=("gl_codes.create",)),
    "glcode.ajax_update_gl_code": requirement(any_of=("gl_codes.edit",)),
    "spoilage.view_spoilage": requirement(any_of=("spoilage.view",)),
    "admin.activate_user": requirement(any_of=("users.manage",)),
    "admin.delete_user": requirement(any_of=("users.manage",)),
    "admin.create_backup_route": requirement(any_of=("backups.create",)),
    "admin.restore_backup_route": requirement(any_of=("backups.restore",)),
    "admin.restore_backup_file": requirement(any_of=("backups.restore",)),
    "admin.download_backup": requirement(any_of=("backups.download",)),
    "admin.activity_logs": requirement(any_of=("activity_logs.view",)),
    "admin.download_example": requirement(any_of=("imports.view", "imports.run")),
    "admin.import_data": requirement(any_of=("imports.run",)),
    "admin.import_page": requirement(any_of=("imports.view", "imports.run")),
    "admin.system_info": requirement(any_of=("system_info.view",)),
    "admin.sales_import_detail": requirement(any_of=("sales_imports.view", "sales_imports.manage")),
    "admin.permission_catalog": requirement(any_of=("permissions.view", "permissions.manage")),
}


ENDPOINT_METHOD_PERMISSION_RULES: dict[tuple[str, str], PermissionRequirement] = {
    ("notes.entity_notes", "GET"): requirement(
        any_of=(
            "locations.view",
            "locations.manage_items",
            "items.view",
            "products.edit",
            "vendors.edit",
            "customers.edit",
            "transfers.view",
            "purchase_orders.edit",
            "purchase_invoices.view",
            "invoices.view",
        )
    ),
    ("notes.entity_notes", "POST"): requirement(
        any_of=(
            "locations.view",
            "locations.manage_items",
            "items.view",
            "products.edit",
            "vendors.edit",
            "customers.edit",
            "transfers.view",
            "purchase_orders.edit",
            "purchase_invoices.view",
            "invoices.view",
        )
    ),
    ("notes.edit_note", "GET"): requirement(
        any_of=(
            "locations.view",
            "locations.manage_items",
            "items.view",
            "products.edit",
            "vendors.edit",
            "customers.edit",
            "transfers.view",
            "purchase_orders.edit",
            "purchase_invoices.view",
            "invoices.view",
        )
    ),
    ("notes.edit_note", "POST"): requirement(
        any_of=(
            "locations.view",
            "locations.manage_items",
            "items.view",
            "products.edit",
            "vendors.edit",
            "customers.edit",
            "transfers.view",
            "purchase_orders.edit",
            "purchase_invoices.view",
            "invoices.view",
        )
    ),
    ("notes.delete_note", "POST"): requirement(
        any_of=(
            "locations.view",
            "locations.manage_items",
            "items.view",
            "products.edit",
            "vendors.edit",
            "customers.edit",
            "transfers.view",
            "purchase_orders.edit",
            "purchase_invoices.view",
            "invoices.view",
        )
    ),
    ("notes.toggle_pin", "POST"): requirement(
        any_of=(
            "locations.view",
            "locations.manage_items",
            "items.view",
            "products.edit",
            "vendors.edit",
            "customers.edit",
            "transfers.view",
            "purchase_orders.edit",
            "purchase_invoices.view",
            "invoices.view",
        )
    ),
    ("item.bulk_update_items", "GET"): requirement(any_of=("items.bulk_update",)),
    ("item.bulk_update_items", "POST"): requirement(any_of=("items.bulk_update",)),
    ("item.item_locations", "GET"): requirement(any_of=("items.view", "locations.manage_items")),
    ("item.item_locations", "POST"): requirement(any_of=("locations.manage_items",)),
    ("item.item_units", "GET"): requirement(any_of=("items.edit", "items.manage_units")),
    ("item.item_units", "POST"): requirement(any_of=("items.manage_units",)),
    ("item.item_last_cost", "GET"): requirement(
        any_of=(
            "items.view",
            "purchase_orders.create",
            "purchase_orders.edit",
            "purchase_invoices.receive",
        )
    ),
    ("item.search_items", "GET"): requirement(
        any_of=(
            "items.view",
            "items.create",
            "items.edit",
            "locations.manage_items",
            "menus.edit",
            "products.create",
            "products.edit",
            "products.manage_recipe",
            "purchase_orders.create",
            "purchase_orders.edit",
            "purchase_invoices.receive",
            "reports.purchase_inventory_summary",
            "reports.inventory_variance",
            "transfers.create",
            "transfers.edit",
        )
    ),
    ("locations.location_items", "GET"): requirement(any_of=("locations.view", "locations.manage_items")),
    ("locations.location_items", "POST"): requirement(any_of=("locations.manage_items",)),
    ("locations.bulk_update_locations", "GET"): requirement(any_of=("locations.bulk_update",)),
    ("locations.bulk_update_locations", "POST"): requirement(any_of=("locations.bulk_update",)),
    ("product.bulk_update_products", "GET"): requirement(any_of=("products.bulk_update",)),
    ("product.bulk_update_products", "POST"): requirement(any_of=("products.bulk_update",)),
    ("product.validate_product_form", "POST"): requirement(any_of=("products.create", "products.edit", "products.manage_recipe")),
    ("product.search_products", "GET"): requirement(
        any_of=(
            "products.view",
            "products.create",
            "products.edit",
            "products.manage_recipe",
            "invoices.create",
            "locations.edit",
            "menus.assign",
            "menus.edit",
            "reports.department_sales_forecast",
        )
    ),
    ("schedule.team_schedule", "GET"): requirement(
        any_of=(
            "schedules.view_team",
            "schedules.edit_team",
            "schedules.publish",
            "schedules.view_labor",
            "schedules.view_seen_status",
            "schedules.self_schedule",
        )
    ),
    ("schedule.team_schedule", "POST"): requirement(
        any_of=(
            "schedules.edit_team",
            "schedules.self_schedule",
            "schedules.delete",
            "schedules.publish",
            "schedules.auto_assign",
        )
    ),
    ("schedule.my_schedule", "GET"): requirement(
        any_of=("schedules.view_self", "schedules.self_schedule")
    ),
    ("schedule.availability", "GET"): requirement(
        any_of=(
            "schedules.manage_self_availability",
            "schedules.manage_team_availability",
        )
    ),
    ("schedule.availability", "POST"): requirement(
        any_of=(
            "schedules.manage_self_availability",
            "schedules.manage_team_availability",
        )
    ),
    ("schedule.time_off", "GET"): requirement(
        any_of=(
            "schedules.request_time_off",
            "schedules.view_self_time_off",
            "schedules.view_team_time_off",
            "schedules.approve_time_off",
        )
    ),
    ("schedule.time_off", "POST"): requirement(
        any_of=(
            "schedules.request_time_off",
            "schedules.approve_time_off",
            "schedules.view_self_time_off",
        )
    ),
    ("schedule.tradeboard", "GET"): requirement(
        any_of=(
            "schedules.view_tradeboard",
            "schedules.claim_tradeboard",
            "schedules.approve_tradeboard",
        )
    ),
    ("schedule.tradeboard", "POST"): requirement(
        any_of=("schedules.claim_tradeboard", "schedules.approve_tradeboard")
    ),
    ("schedule.setup", "GET"): requirement(
        any_of=("schedules.manage_setup", "schedules.manage_pay_rates")
    ),
    ("schedule.setup", "POST"): requirement(any_of=("schedules.manage_setup",)),
    ("schedule.user_settings", "GET"): requirement(
        any_of=("schedules.manage_setup", "schedules.manage_pay_rates")
    ),
    ("schedule.user_settings", "POST"): requirement(
        any_of=("schedules.manage_setup", "schedules.manage_pay_rates")
    ),
    ("communication.center", "GET"): requirement(
        any_of=(
            "communications.view",
            "communications.view_history",
            "communications.send_direct",
            "communications.send_broadcast",
            "communications.manage_bulletin",
        )
    ),
    ("communication.center", "POST"): requirement(
        any_of=(
            "communications.view",
            "communications.view_history",
            "communications.send_direct",
            "communications.send_broadcast",
            "communications.manage_bulletin",
        )
    ),
    ("admin.user_profile", "GET"): requirement(any_of=("users.manage",)),
    ("admin.user_profile", "POST"): requirement(any_of=("users.manage",)),
    ("admin.users", "GET"): requirement(any_of=("users.view", "users.manage")),
    ("admin.users", "POST"): requirement(any_of=("users.manage",)),
    ("admin.backups", "GET"): requirement(any_of=("backups.view", "backups.create", "backups.restore", "backups.download")),
    ("admin.settings", "GET"): requirement(any_of=("settings.view", "settings.manage")),
    ("admin.settings", "POST"): requirement(any_of=("settings.manage",)),
    ("admin.terminal_sales_mappings", "GET"): requirement(any_of=("terminal_sales_mappings.view", "terminal_sales_mappings.manage")),
    ("admin.terminal_sales_mappings", "POST"): requirement(any_of=("terminal_sales_mappings.manage",)),
    ("admin.sales_imports", "GET"): requirement(any_of=("sales_imports.view", "sales_imports.manage")),
    ("admin.sales_imports", "POST"): requirement(any_of=("sales_imports.manage",)),
    ("admin.sales_import_detail", "GET"): requirement(any_of=("sales_imports.view", "sales_imports.manage")),
    ("admin.sales_import_detail", "POST"): requirement(any_of=("sales_imports.manage",)),
    ("admin.vendor_item_aliases", "GET"): requirement(any_of=("vendor_item_aliases.view", "vendor_item_aliases.manage")),
    ("admin.vendor_item_aliases", "POST"): requirement(any_of=("vendor_item_aliases.manage",)),
    ("admin.delete_vendor_item_alias", "POST"): requirement(any_of=("vendor_item_aliases.manage",)),
    ("admin.permission_groups", "GET"): requirement(any_of=("permission_groups.view", "permission_groups.manage")),
    ("admin.create_permission_group", "GET"): requirement(any_of=("permission_groups.manage",)),
    ("admin.create_permission_group", "POST"): requirement(any_of=("permission_groups.manage",)),
    ("admin.edit_permission_group", "GET"): requirement(any_of=("permission_groups.view", "permission_groups.manage", "permissions.view", "permissions.manage")),
    ("admin.edit_permission_group", "POST"): requirement(any_of=("permission_groups.manage", "permissions.manage")),
    ("admin.delete_permission_group", "POST"): requirement(any_of=("permission_groups.manage",)),
    ("admin.user_access", "GET"): requirement(any_of=("users.view", "users.manage")),
    ("admin.user_access", "POST"): requirement(any_of=("users.manage",)),
}


DEFAULT_LANDING_ENDPOINTS: tuple[str, ...] = (
    "transfer.view_transfers",
    "main.home",
    "admin.users",
    "admin.settings",
    "admin.permission_groups",
    "admin.permission_catalog",
    "admin.sales_imports",
    "admin.vendor_item_aliases",
    "admin.terminal_sales_mappings",
    "admin.backups",
    "admin.system_info",
    "admin.activity_logs",
    "schedule.team_schedule",
    "schedule.my_schedule",
    "invoice.view_invoices",
    "purchase.view_purchase_orders",
    "item.view_items",
    "locations.view_locations",
    "menu.view_menus",
    "product.view_products",
    "customer.view_customers",
    "vendor.view_vendors",
    "event.view_events",
    "auth.profile",
)


def get_permission_categories() -> list[dict[str, object]]:
    grouped: list[dict[str, object]] = []
    for category, label in PERMISSION_CATEGORY_LABELS.items():
        permissions = [
            definition
            for definition in PERMISSION_DEFINITIONS
            if definition.category == category
        ]
        if permissions:
            grouped.append(
                {
                    "key": category,
                    "label": label,
                    "permissions": permissions,
                }
            )
    return grouped


def get_permission_definition(code: str) -> PermissionDefinition | None:
    return PERMISSION_DEFINITIONS_BY_CODE.get(code)


def get_permission_requirement(
    endpoint: str | None, method: str = "GET"
) -> PermissionRequirement | None:
    if not endpoint:
        return None
    normalized_method = (method or "GET").upper()
    return ENDPOINT_METHOD_PERMISSION_RULES.get(
        (endpoint, normalized_method)
    ) or ENDPOINT_PERMISSION_RULES.get(endpoint)


def _is_super_admin_user(user) -> bool:
    return bool(
        getattr(
            user,
            "is_super_admin",
            getattr(user, "is_admin", False),
        )
    )


def user_can_access_endpoint(user, endpoint: str | None, method: str = "GET") -> bool:
    if not endpoint or endpoint == "static":
        return True

    requirement_to_check = get_permission_requirement(endpoint, method)
    if requirement_to_check is None:
        return True

    if _is_super_admin_user(user):
        return True

    if not getattr(user, "is_authenticated", False):
        return False

    if requirement_to_check.all_of and not all(
        user.has_permission(code) for code in requirement_to_check.all_of
    ):
        return False

    if requirement_to_check.any_of and not any(
        user.has_permission(code) for code in requirement_to_check.any_of
    ):
        return False

    return True


def get_default_landing_endpoint(user) -> str:
    for endpoint in DEFAULT_LANDING_ENDPOINTS:
        if endpoint not in current_app.view_functions:
            continue
        if endpoint == "auth.profile":
            return endpoint
        if user_can_access_endpoint(user, endpoint, "GET"):
            return endpoint
    return "auth.profile"


def sync_permission_data(session) -> None:
    from app.models import Permission, PermissionGroup, Setting

    permissions_by_code = {
        permission.code: permission for permission in Permission.query.all()
    }
    changed = False

    for definition in PERMISSION_DEFINITIONS:
        permission = permissions_by_code.get(definition.code)
        if permission is None:
            session.add(
                Permission(
                    code=definition.code,
                    category=definition.category,
                    label=definition.label,
                    description=definition.description,
                )
            )
            changed = True
            continue

        if permission.category != definition.category:
            permission.category = definition.category
            changed = True
        if permission.label != definition.label:
            permission.label = definition.label
            changed = True
        if permission.description != definition.description:
            permission.description = definition.description
            changed = True

    if changed:
        session.flush()

    legacy_group = PermissionGroup.query.filter_by(
        key="legacy_full_app_access"
    ).first()
    if legacy_group is not None:
        legacy_group.users = []
        legacy_group.permissions = []
        session.delete(legacy_group)
        changed = True

    legacy_backfill_setting = Setting.query.filter_by(
        name="PERMISSIONS_BACKFILL_DONE"
    ).first()
    if legacy_backfill_setting is not None:
        session.delete(legacy_backfill_setting)
        changed = True

    if changed:
        session.commit()
