from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import types
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPS_DIR = REPO_ROOT / ".codex-pydeps311"
if DEPS_DIR.exists():
    sys.path.insert(0, str(DEPS_DIR))
sys.path.insert(0, str(REPO_ROOT))


def _install_weasyprint_stub() -> None:
    """Allow the app to boot for UI auditing without native WeasyPrint deps."""

    if "weasyprint" in sys.modules:
        return

    weasy = types.ModuleType("weasyprint")
    formatting_structure = types.ModuleType("weasyprint.formatting_structure")
    boxes = types.ModuleType("weasyprint.formatting_structure.boxes")

    class _Dummy:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def write_pdf(self, *args: Any, **kwargs: Any) -> bytes:
            return b""

    class TableCellBox:
        pass

    weasy.CSS = _Dummy
    weasy.HTML = _Dummy
    boxes.TableCellBox = TableCellBox
    formatting_structure.boxes = boxes
    weasy.formatting_structure = formatting_structure

    sys.modules["weasyprint"] = weasy
    sys.modules["weasyprint.formatting_structure"] = formatting_structure
    sys.modules["weasyprint.formatting_structure.boxes"] = boxes


_install_weasyprint_stub()

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import create_admin_user, create_app, db, limiter
from app.models import (
    Customer,
    Event,
    EventLocation,
    EventStandSheetItem,
    GLCode,
    Invoice,
    InvoiceProduct,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Menu,
    Note,
    PosSalesImport,
    PosSalesImportLocation,
    PosSalesImportRow,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Setting,
    TerminalSale,
    Transfer,
    TransferItem,
    User,
    Vendor,
    VendorItemAlias,
)
from app.utils.units import (
    DEFAULT_BASE_UNIT_CONVERSIONS,
    serialize_conversion_setting,
)

CHROME_PATH = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
OUTPUT_ROOT = REPO_ROOT / "artifacts" / "mobile-audit"
DB_PATH = OUTPUT_ROOT / "mobile_audit.db"
REPORT_PATH = OUTPUT_ROOT / "report.md"
JSON_PATH = OUTPUT_ROOT / "report.json"
SCREENSHOT_DIR = OUTPUT_ROOT / "screenshots"

VIEWPORTS: list[dict[str, int | str]] = [
    {"label": "414x896", "width": 414, "height": 896},
    {"label": "390x844", "width": 390, "height": 844},
    {"label": "393x852", "width": 393, "height": 852},
    {"label": "384x832", "width": 384, "height": 832},
    {"label": "402x874", "width": 402, "height": 874},
]

PUBLIC_SKIP_REASONS = {
    "auth/reset_token.html": "Requires a valid password-reset token.",
}

WORKFLOW_SKIP_REASONS = {
    "report_vendor_invoice_results.html": "Results screen depends on prior filter submission.",
    "report_received_invoices_results.html": "Results screen depends on prior filter submission.",
    "report_product_recipe_results.html": "Results screen depends on prior filter submission.",
    "transfers/view_report.html": "Report screen depends on generated report query parameters.",
}

AUDIT_JS = """
() => {
  const viewportWidth = window.innerWidth;
  const scrolling = document.scrollingElement || document.documentElement;
  const rootOverflow = scrolling.scrollWidth > viewportWidth + 2;
  const interactiveSelector = 'a[href], button, input:not([type="hidden"]), select, textarea, [role="button"]';
  const visible = (el) => {
    const hiddenAncestor = el.closest(
      '.offcanvas:not(.show), .dropdown-menu:not(.show), .modal:not(.show), [hidden], .d-none'
    );
    if (hiddenAncestor) {
      return false;
    }
    if (el.closest('[aria-hidden="true"]')) {
      return false;
    }
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
  };

  let offscreenInteractiveCount = 0;
  let undersizedInteractiveCount = 0;
  for (const el of document.querySelectorAll(interactiveSelector)) {
    if (!visible(el)) continue;
    const rect = el.getBoundingClientRect();
    if (rect.left < -1 || rect.right > viewportWidth + 1) {
      offscreenInteractiveCount += 1;
    }
    const isPlainTextLink =
      el.tagName.toLowerCase() === 'a' &&
      !el.classList.contains('btn') &&
      !el.closest('.btn, .btn-group, .nav, .pagination');
    if (!isPlainTextLink && (rect.width < 32 || rect.height < 32)) {
      undersizedInteractiveCount += 1;
    }
  }

  let nowrapOverflowCount = 0;
  for (const el of document.querySelectorAll('*')) {
    const style = window.getComputedStyle(el);
    if (!style.display.includes('flex') || style.flexWrap !== 'nowrap') continue;
    if (el.clientWidth > 0 && el.scrollWidth > el.clientWidth + 2) {
      nowrapOverflowCount += 1;
    }
  }

  let unwrappedTableCount = 0;
  for (const table of document.querySelectorAll('table')) {
    if (!table.closest('.table-responsive') && !table.classList.contains('table-mobile-card')) {
      unwrappedTableCount += 1;
    }
  }

  return {
    rootOverflow,
    scrollWidth: scrolling.scrollWidth,
    viewportWidth,
    offscreenInteractiveCount,
    undersizedInteractiveCount,
    nowrapOverflowCount,
    unwrappedTableCount,
    tableCount: document.querySelectorAll('table').length,
  };
}
"""


@dataclass(frozen=True)
class Case:
    name: str
    path: str
    template: str
    auth_required: bool = True
    notes: str = ""


@dataclass
class AuditRun:
    viewport: str
    width: int
    height: int
    status_code: int | None
    final_url: str
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    screenshot: str | None = None
    error: str | None = None


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _ensure_setting(name: str, value: str) -> None:
    setting = Setting.query.filter_by(name=name).first()
    if setting is None:
        setting = Setting(name=name, value=value)
        db.session.add(setting)
    else:
        setting.value = value


def build_app() -> Any:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    os.environ["SECRET_KEY"] = "mobile-audit-secret"
    os.environ["ADMIN_EMAIL"] = "admin@example.com"
    os.environ["ADMIN_PASS"] = "adminpass"
    os.environ["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH.as_posix()}"
    os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
    os.environ["ENFORCE_HTTPS"] = "0"
    os.environ["SUPPORT_MODE"] = "0"

    app, _ = create_app(["--demo"])
    app.config.update(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": True,
            "RATELIMIT_ENABLED": False,
            "SERVER_NAME": None,
        }
    )
    limiter.enabled = False
    limiter_extension = app.extensions.get("limiter")
    if hasattr(limiter_extension, "enabled"):
        limiter_extension.enabled = False
    return app


def seed_data(app: Any) -> dict[str, Any]:
    with app.app_context():
        create_admin_user()
        admin = User.query.filter_by(is_admin=True).first()
        if admin is None:
            raise RuntimeError("Admin user was not created for the mobile audit.")

        _ensure_setting("GST", "")
        _ensure_setting("DEFAULT_TIMEZONE", "America/Winnipeg")
        _ensure_setting(
            "BASE_UNIT_CONVERSIONS",
            serialize_conversion_setting(DEFAULT_BASE_UNIT_CONVERSIONS),
        )

        gl_purchase = GLCode(code="4000", description="Purchases")
        gl_sales = GLCode(code="5000", description="Sales")
        db.session.add_all([gl_purchase, gl_sales])
        db.session.flush()

        customer = Customer(first_name="Mobile", last_name="Customer")
        vendor = Vendor(first_name="Mobile", last_name="Vendor")
        menu = Menu(name="Mobile Menu")
        location = Location(name="Mobile Stand")
        warehouse = Location(name="Mobile Warehouse")
        spoilage = Location(name="Mobile Spoilage", is_spoilage=True)
        db.session.add_all([customer, vendor, menu, location, warehouse, spoilage])
        db.session.flush()

        item = Item(
            name="Mobile Item",
            base_unit="each",
            quantity=18.0,
            cost=2.25,
            gl_code_id=gl_purchase.id,
            purchase_gl_code_id=gl_purchase.id,
        )
        db.session.add(item)
        db.session.flush()

        item_unit = ItemUnit(
            item_id=item.id,
            name="case",
            factor=6.0,
            receiving_default=True,
            transfer_default=True,
        )
        product = Product(
            name="Mobile Product",
            price=8.5,
            cost=3.5,
            gl_code_id=gl_purchase.id,
            sales_gl_code_id=gl_sales.id,
        )
        db.session.add_all([item_unit, product])
        db.session.flush()

        product.locations.extend([location, warehouse])
        menu.products.append(product)
        location.current_menu_id = menu.id
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=item_unit.id,
                quantity=1.0,
                countable=True,
            )
        )
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=24.0,
                purchase_gl_code_id=gl_purchase.id,
            )
        )
        db.session.add(
            LocationStandItem(
                location_id=warehouse.id,
                item_id=item.id,
                expected_count=32.0,
                purchase_gl_code_id=gl_purchase.id,
            )
        )
        db.session.flush()

        invoice = Invoice(id="MOBILEINV001", user_id=admin.id, customer_id=customer.id)
        db.session.add(invoice)
        db.session.flush()
        db.session.add(
            InvoiceProduct(
                invoice_id=invoice.id,
                quantity=2.0,
                product_id=product.id,
                product_name=product.name,
                unit_price=product.price,
                line_subtotal=17.0,
                line_gst=0.0,
                line_pst=0.0,
            )
        )

        purchase_order = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=admin.id,
            vendor_name="Mobile Vendor",
            order_number="PO-1001",
            order_date=date.today(),
            expected_date=date.today(),
            expected_total_cost=15.0,
            delivery_charge=1.25,
            received=False,
        )
        db.session.add(purchase_order)
        db.session.flush()
        db.session.add(
            PurchaseOrderItem(
                purchase_order_id=purchase_order.id,
                position=0,
                item_id=item.id,
                unit_id=item_unit.id,
                quantity=5.0,
                unit_cost=2.25,
            )
        )

        purchase_invoice = PurchaseInvoice(
            purchase_order_id=purchase_order.id,
            user_id=admin.id,
            location_id=location.id,
            vendor_name="Mobile Vendor",
            location_name=location.name,
            received_date=date.today(),
            invoice_number="PINV-2001",
            department="food",
            gst=0.0,
            pst=0.0,
            delivery_charge=1.25,
        )
        db.session.add(purchase_invoice)
        db.session.flush()
        db.session.add(
            PurchaseInvoiceItem(
                invoice_id=purchase_invoice.id,
                position=0,
                item_id=item.id,
                unit_id=item_unit.id,
                item_name=item.name,
                unit_name=item_unit.name,
                quantity=5.0,
                cost=2.25,
                container_deposit=0.0,
            )
        )

        transfer = Transfer(
            from_location_id=warehouse.id,
            to_location_id=location.id,
            user_id=admin.id,
            from_location_name=warehouse.name,
            to_location_name=location.name,
            completed=False,
        )
        db.session.add(transfer)
        db.session.flush()
        db.session.add(
            TransferItem(
                transfer_id=transfer.id,
                item_id=item.id,
                quantity=3.0,
                completed_quantity=1.0,
                unit_id=item_unit.id,
                unit_quantity=3.0,
                base_quantity=18.0,
                item_name=item.name,
            )
        )

        event = Event(
            name="Mobile Event",
            start_date=date.today(),
            end_date=date.today(),
            event_type="festival",
        )
        db.session.add(event)
        db.session.flush()
        event_location = EventLocation(
            event_id=event.id,
            location_id=location.id,
            opening_count=12.0,
            closing_count=8.0,
            confirmed=False,
        )
        db.session.add(event_location)
        db.session.flush()
        db.session.add(
            TerminalSale(
                event_location_id=event_location.id,
                product_id=product.id,
                quantity=4.0,
            )
        )
        db.session.add(
            EventStandSheetItem(
                event_location_id=event_location.id,
                item_id=item.id,
                opening_count=12.0,
                transferred_in=2.0,
                transferred_out=1.0,
                adjustments=0.0,
                eaten=0.0,
                spoiled=1.0,
                closing_count=8.0,
            )
        )

        closed_event = Event(
            name="Closed Mobile Event",
            start_date=date.today(),
            end_date=date.today(),
            event_type="festival",
            closed=True,
        )
        db.session.add(closed_event)
        db.session.flush()
        closed_event_location = EventLocation(
            event_id=closed_event.id,
            location_id=warehouse.id,
            opening_count=10.0,
            closing_count=7.0,
            confirmed=True,
        )
        db.session.add(closed_event_location)
        db.session.flush()
        db.session.add(
            TerminalSale(
                event_location_id=closed_event_location.id,
                product_id=product.id,
                quantity=3.0,
            )
        )
        db.session.add(
            EventStandSheetItem(
                event_location_id=closed_event_location.id,
                item_id=item.id,
                opening_count=10.0,
                transferred_in=1.0,
                transferred_out=0.0,
                adjustments=0.0,
                eaten=0.0,
                spoiled=0.0,
                closing_count=7.0,
            )
        )

        inventory_event = Event(
            name="Inventory Count Event",
            start_date=date.today(),
            end_date=date.today(),
            event_type="inventory",
        )
        db.session.add(inventory_event)
        db.session.flush()
        inventory_event_location = EventLocation(
            event_id=inventory_event.id,
            location_id=warehouse.id,
            opening_count=0.0,
            closing_count=0.0,
            confirmed=False,
        )
        db.session.add(inventory_event_location)

        alias = VendorItemAlias(
            vendor_id=vendor.id,
            item_id=item.id,
            item_unit_id=item_unit.id,
            vendor_sku="MOBILE-SKU",
            vendor_description="Mobile audit item",
            normalized_description="mobile audit item",
            pack_size="6x1",
            default_cost=2.25,
        )
        db.session.add(alias)

        note = Note(
            entity_type="item",
            entity_id=str(item.id),
            user_id=admin.id,
            content="Seed note for mobile usability audit.",
        )
        db.session.add(note)

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="mobile-audit-import",
            attachment_filename="mobile-audit.xls",
            attachment_sha256="a" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()
        sales_import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name=location.name,
            normalized_location_name="mobile-stand",
            location_id=location.id,
            total_quantity=4.0,
            net_inc=34.0,
            discounts_abs=1.0,
            computed_total=33.0,
            parse_index=0,
        )
        db.session.add(sales_import_location)
        db.session.flush()
        db.session.add(
            PosSalesImportRow(
                import_id=sales_import.id,
                location_import_id=sales_import_location.id,
                source_product_name=product.name,
                normalized_product_name="mobile-product",
                product_id=product.id,
                quantity=4.0,
                net_inc=33.0,
                discount_raw="-1.00",
                discount_abs=1.0,
                computed_line_total=33.0,
                computed_unit_price=8.25,
                parse_index=0,
            )
        )

        db.session.commit()

        return {
            "admin_id": admin.id,
            "customer_id": customer.id,
            "vendor_id": vendor.id,
            "gl_code_id": gl_purchase.id,
            "item_id": item.id,
            "item_note_id": note.id,
            "location_id": location.id,
            "menu_id": menu.id,
            "product_id": product.id,
            "event_id": event.id,
            "event_location_id": event_location.id,
            "closed_event_id": closed_event.id,
            "inventory_event_id": inventory_event.id,
            "invoice_id": invoice.id,
            "inventory_location_id": warehouse.id,
            "purchase_order_id": purchase_order.id,
            "purchase_invoice_id": purchase_invoice.id,
            "transfer_id": transfer.id,
            "sales_import_id": sales_import.id,
        }


def build_cases(ids: dict[str, Any]) -> tuple[list[Case], list[dict[str, str]]]:
    cases = [
        Case("Login", "/auth/login", "auth/login.html", auth_required=False),
        Case(
            "Reset Request",
            "/auth/reset",
            "auth/reset_request.html",
            auth_required=False,
        ),
        Case("Zero Threat", "/zero-threat.html", "auth/zero-threat.html"),
        Case("Dashboard", "/", "dashboard.html"),
        Case("Profile", "/auth/profile", "profile.html"),
        Case("Users", "/controlpanel/users", "admin/view_users.html"),
        Case("Backups", "/controlpanel/backups", "admin/backups.html"),
        Case("Activity Logs", "/controlpanel/activity", "admin/activity_logs.html"),
        Case("System Info", "/controlpanel/system", "admin/system_info.html"),
        Case("Data Imports", "/controlpanel/imports", "admin/imports.html"),
        Case("Settings", "/controlpanel/settings", "admin/settings.html"),
        Case(
            "Terminal Sales Mappings",
            "/controlpanel/terminal-sales-mappings",
            "admin/terminal_sales_mappings.html",
        ),
        Case(
            "Sales Imports",
            "/controlpanel/sales-imports",
            "admin/sales_imports.html",
        ),
        Case(
            "Sales Import Detail",
            f"/controlpanel/sales-imports/{ids['sales_import_id']}",
            "admin/sales_import_detail.html",
        ),
        Case(
            "Vendor Item Aliases",
            "/controlpanel/vendor-item-aliases",
            "admin/vendor_item_aliases.html",
        ),
        Case("Customers", "/customers", "customers/view_customers.html"),
        Case(
            "Customer Form",
            f"/customers/{ids['customer_id']}/edit",
            "customers/customer_form_page.html",
        ),
        Case("Events", "/events", "events/view_events.html"),
        Case(
            "Event Form",
            f"/events/{ids['event_id']}/edit",
            "events/edit_event.html",
        ),
        Case(
            "Event View",
            f"/events/{ids['event_id']}",
            "events/view_event.html",
        ),
        Case(
            "Close Report",
            f"/events/{ids['closed_event_id']}/close-report",
            "events/close_report.html",
        ),
        Case(
            "Add Event Location",
            f"/events/{ids['event_id']}/add_location",
            "events/add_location.html",
        ),
        Case(
            "Add Terminal Sales",
            (
                f"/events/{ids['event_id']}/locations/"
                f"{ids['event_location_id']}/sales/add"
            ),
            "events/add_terminal_sales.html",
        ),
        Case(
            "Scan Count",
            (
                f"/events/{ids['inventory_event_id']}/locations/"
                f"{ids['inventory_location_id']}/scan_counts"
            ),
            "events/scan_count.html",
        ),
        Case(
            "Upload Terminal Sales",
            f"/events/{ids['event_id']}/terminal-sales",
            "events/upload_terminal_sales.html",
        ),
        Case(
            "Confirm Location",
            (
                f"/events/{ids['event_id']}/locations/"
                f"{ids['event_location_id']}/confirm"
            ),
            "events/confirm_location.html",
        ),
        Case(
            "Event Stand Sheet",
            f"/events/{ids['event_id']}/stand_sheet/{ids['location_id']}",
            "events/stand_sheet.html",
        ),
        Case(
            "Sustainability Dashboard",
            f"/events/{ids['event_id']}/sustainability",
            "events/sustainability_dashboard.html",
        ),
        Case(
            "Count Sheet",
            f"/events/{ids['event_id']}/count_sheet/{ids['location_id']}",
            "events/count_sheet.html",
        ),
        Case(
            "Bulk Stand Sheets",
            f"/events/{ids['event_id']}/stand_sheets",
            "events/bulk_stand_sheets.html",
        ),
        Case(
            "Bulk Count Sheets",
            f"/events/{ids['event_id']}/count_sheets",
            "events/bulk_count_sheets.html",
        ),
        Case(
            "Inventory Report",
            f"/events/{ids['event_id']}/inventory_report",
            "events/inventory_report.html",
        ),
        Case("GL Codes", "/gl_codes", "gl_codes/view_gl_codes.html"),
        Case(
            "GL Code Form",
            f"/gl_codes/{ids['gl_code_id']}/edit",
            "gl_codes/edit_gl_code.html",
        ),
        Case("Invoices", "/view_invoices", "invoices/view_invoices.html"),
        Case("Create Invoice", "/create_invoice", "invoices/create_invoice.html"),
        Case(
            "View Invoice",
            f"/view_invoice/{ids['invoice_id']}",
            "invoices/view_invoice.html",
        ),
        Case("Items", "/items", "items/view_items.html"),
        Case(
            "Recipe Calculator",
            "/items/recipe-cost-calculator",
            "items/recipe_calculator.html",
        ),
        Case("View Item", f"/items/{ids['item_id']}", "items/view_item.html"),
        Case(
            "Item Locations",
            f"/items/{ids['item_id']}/locations",
            "items/item_locations.html",
        ),
        Case(
            "Item Form",
            f"/items/edit/{ids['item_id']}",
            "items/item_form_page.html",
        ),
        Case("Import Items", "/import_items", "items/import_items.html"),
        Case("Locations", "/locations", "locations/view_locations.html"),
        Case(
            "Location Form",
            f"/locations/edit/{ids['location_id']}",
            "locations/edit_location.html",
        ),
        Case(
            "Location Stand Sheet",
            f"/locations/{ids['location_id']}/stand_sheet",
            "locations/stand_sheet.html",
        ),
        Case(
            "Location Items",
            f"/locations/{ids['location_id']}/items",
            "locations/location_items.html",
        ),
        Case("Menus", "/menus", "menus/view_menus.html"),
        Case(
            "Menu Form",
            f"/menus/{ids['menu_id']}/edit",
            "menus/edit_menu.html",
        ),
        Case(
            "Assign Menu",
            f"/menus/{ids['menu_id']}/assign",
            "menus/assign_menu.html",
        ),
        Case("Notes", f"/notes/item/{ids['item_id']}", "notes/entity_notes.html"),
        Case(
            "Edit Note",
            f"/notes/item/{ids['item_id']}/edit/{ids['item_note_id']}",
            "notes/edit_note.html",
        ),
        Case("Products", "/products", "products/view_products.html"),
        Case(
            "Product Form",
            f"/products/{ids['product_id']}/edit",
            "products/edit_product.html",
        ),
        Case(
            "Product Recipe",
            f"/products/{ids['product_id']}/recipe",
            "products/edit_product_recipe.html",
        ),
        Case(
            "Purchase Orders",
            "/purchase_orders",
            "purchase_orders/view_purchase_orders.html",
        ),
        Case(
            "Resolve Vendor Items",
            "/purchase_orders/resolve_vendor_items",
            "purchase_orders/resolve_vendor_items.html",
        ),
        Case(
            "Purchase Order Form",
            f"/purchase_orders/edit/{ids['purchase_order_id']}",
            "purchase_orders/edit_purchase_order.html",
        ),
        Case(
            "Purchase Recommendations",
            "/purchase_orders/recommendations",
            "purchase_orders/recommendations.html",
        ),
        Case(
            "Receive Invoice",
            f"/purchase_orders/{ids['purchase_order_id']}/receive",
            "purchase_orders/receive_invoice.html",
        ),
        Case(
            "Purchase Invoices",
            "/purchase_invoices",
            "purchase_invoices/view_purchase_invoices.html",
        ),
        Case(
            "Purchase Invoice",
            f"/purchase_invoices/{ids['purchase_invoice_id']}",
            "purchase_invoices/view_purchase_invoice.html",
        ),
        Case(
            "Purchase Invoice GL Report",
            f"/purchase_invoices/{ids['purchase_invoice_id']}/report",
            "report_invoice_gl_code.html",
        ),
        Case(
            "Reverse Purchase Invoice",
            f"/purchase_invoices/{ids['purchase_invoice_id']}/reverse",
            "confirm_action.html",
        ),
        Case(
            "Department Sales Forecast",
            "/reports/department-sales-forecast",
            "report_department_sales_forecast.html",
        ),
        Case(
            "Vendor Invoices Report",
            "/reports/vendor-invoices",
            "report_vendor_invoices.html",
        ),
        Case(
            "Received Invoices Report",
            "/reports/received-invoices",
            "report_received_invoices.html",
        ),
        Case(
            "Purchase Inventory Summary",
            "/reports/purchase-inventory-summary",
            "report_purchase_inventory_summary.html",
        ),
        Case(
            "Inventory Variance",
            "/reports/inventory-variance",
            "report_inventory_variance.html",
        ),
        Case(
            "Product Sales Report",
            "/reports/product-sales",
            "report_product_sales.html",
        ),
        Case(
            "Product Stock Usage",
            "/reports/product-stock-usage",
            "report_product_stock_usage.html",
        ),
        Case(
            "Product Recipes Report",
            "/reports/product-recipes",
            "report_product_recipe.html",
        ),
        Case(
            "Product Location Sales",
            "/reports/product-location-sales",
            "report_product_location_sales.html",
        ),
        Case(
            "Event Terminal Sales Report",
            "/reports/event-terminal-sales",
            "report_event_terminal_sales.html",
        ),
        Case(
            "Purchase Cost Forecast",
            "/reports/purchase-cost-forecast",
            "report_purchase_cost_forecast.html",
        ),
        Case("Spoilage", "/spoilage", "spoilage/view_spoilage.html"),
        Case("Transfers", "/transfers", "transfers/view_transfers.html"),
        Case(
            "Transfer Form",
            f"/transfers/edit/{ids['transfer_id']}",
            "transfers/edit_transfer.html",
        ),
        Case(
            "Transfer View",
            f"/transfers/view/{ids['transfer_id']}",
            "transfers/view_transfer.html",
        ),
        Case(
            "Transfer Report Form",
            "/transfers/generate_report",
            "transfers/generate_report.html",
        ),
        Case("Vendors", "/vendors", "vendors/view_vendors.html"),
        Case(
            "Vendor Form",
            f"/vendors/{ids['vendor_id']}/edit",
            "vendors/vendor_form_page.html",
        ),
    ]

    skipped = [
        {"template": template, "reason": reason}
        for template, reason in {**PUBLIC_SKIP_REASONS, **WORKFLOW_SKIP_REASONS}.items()
    ]
    return cases, skipped


class ServerThread(threading.Thread):
    def __init__(self, app: Any, port: int) -> None:
        super().__init__(daemon=True)
        self.server = make_server("127.0.0.1", port, app, threaded=True)

    def run(self) -> None:
        self.server.serve_forever()

    def shutdown(self) -> None:
        self.server.shutdown()


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def login(page: Any, base_url: str) -> None:
    response = page.goto(f"{base_url}/auth/login", wait_until="load")
    if response is None or response.status >= 400:
        raise RuntimeError("Unable to load login page for mobile audit.")
    page.fill('input[name="email"]', "admin@example.com")
    page.fill('input[name="password"]', "adminpass")
    page.click('button[type="submit"]')
    page.wait_for_url(re.compile(rf"^{re.escape(base_url)}/(?!auth/login).*"))


def run_case(page: Any, case: Case, base_url: str, viewport: dict[str, Any]) -> AuditRun:
    run = AuditRun(
        viewport=str(viewport["label"]),
        width=int(viewport["width"]),
        height=int(viewport["height"]),
        status_code=None,
        final_url="",
    )
    url = f"{base_url}{case.path}"
    try:
        response = page.goto(url, wait_until="load", timeout=20000)
        page.wait_for_timeout(250)
        run.status_code = response.status if response is not None else None
        run.final_url = page.url
        run.metrics = page.evaluate(AUDIT_JS)
        if run.status_code != 200:
            run.issues.append(f"HTTP {run.status_code}")
        if run.metrics.get("rootOverflow"):
            run.issues.append("page_overflows_horizontally")
        if run.metrics.get("offscreenInteractiveCount", 0):
            run.issues.append("offscreen_interactives")
        if run.metrics.get("nowrapOverflowCount", 0):
            run.issues.append("nowrap_flex_overflow")
        if run.metrics.get("unwrappedTableCount", 0):
            run.issues.append("table_missing_responsive_wrapper")
        if run.issues:
            case_dir = SCREENSHOT_DIR / slugify(case.template)
            case_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = case_dir / f"{viewport['label']}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            run.screenshot = str(screenshot_path.relative_to(REPO_ROOT))
    except PlaywrightError as exc:
        run.error = str(exc)
        run.final_url = page.url
        run.issues.append("playwright_error")
    except Exception as exc:  # pragma: no cover - defensive reporting
        run.error = str(exc)
        run.final_url = page.url
        run.issues.append("unexpected_error")
    return run


def generate_report(
    results: list[dict[str, Any]],
    skipped: list[dict[str, str]],
) -> tuple[dict[str, Any], str]:
    total_runs = sum(len(entry["runs"]) for entry in results)
    total_cases = len(results)
    failing_cases = [entry for entry in results if entry["has_issues"]]
    issue_counter: dict[str, int] = {}
    for entry in failing_cases:
        for run in entry["runs"]:
            for issue in run["issues"]:
                issue_counter[issue] = issue_counter.get(issue, 0) + 1

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "viewports": VIEWPORTS,
        "cases_tested": total_cases,
        "runs_tested": total_runs,
        "failing_cases": len(failing_cases),
        "skipped_templates": len(skipped),
        "issue_counter": issue_counter,
        "results": results,
        "skipped": skipped,
    }

    lines = [
        "# Mobile Usability Audit",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Target phone viewports (StatCounter, United States, March 2026):",
    ]
    for viewport in VIEWPORTS:
        lines.append(f"- `{viewport['label']}`")
    lines.extend(
        [
            "",
            f"Audited `{total_cases}` unique full-page templates across `{total_runs}` viewport runs.",
            f"Flagged `{len(failing_cases)}` templates with at least one mobile issue.",
            "",
            "Issue counts:",
        ]
    )
    if issue_counter:
        for issue, count in sorted(
            issue_counter.items(), key=lambda item: (-item[1], item[0])
        ):
            lines.append(f"- `{issue}`: {count}")
    else:
        lines.append("- None")

    lines.extend(["", "Flagged templates:"])
    if failing_cases:
        for entry in failing_cases:
            issue_viewports = [
                f"{run['viewport']} ({', '.join(run['issues'])})"
                for run in entry["runs"]
                if run["issues"]
            ]
            lines.append(
                f"- `{entry['template']}` via `{entry['path']}`: "
                + "; ".join(issue_viewports)
            )
    else:
        lines.append("- None")

    lines.extend(["", "Skipped templates:"])
    if skipped:
        for entry in skipped:
            lines.append(f"- `{entry['template']}`: {entry['reason']}")
    else:
        lines.append("- None")

    return summary, "\n".join(lines) + "\n"


def main() -> int:
    if not CHROME_PATH.exists():
        raise FileNotFoundError(f"Chrome not found at {CHROME_PATH}")

    app = build_app()
    ids = seed_data(app)
    cases, skipped = build_cases(ids)

    port = find_free_port()
    server = ServerThread(app, port)
    server.start()
    base_url = f"http://127.0.0.1:{port}"
    time.sleep(0.5)

    results: list[dict[str, Any]] = []

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=str(CHROME_PATH),
                headless=True,
            )

            case_results: dict[str, dict[str, Any]] = {
                case.template: {
                    "name": case.name,
                    "path": case.path,
                    "template": case.template,
                    "auth_required": case.auth_required,
                    "runs": [],
                    "has_issues": False,
                }
                for case in cases
            }

            for viewport in VIEWPORTS:
                context_kwargs = {
                    "viewport": {
                        "width": int(viewport["width"]),
                        "height": int(viewport["height"]),
                    }
                }
                public_context = browser.new_context(**context_kwargs)
                auth_context = browser.new_context(**context_kwargs)
                public_page = public_context.new_page()
                auth_page = auth_context.new_page()
                login(auth_page, base_url)

                for case in cases:
                    page = auth_page if case.auth_required else public_page
                    run = run_case(page, case, base_url, viewport)
                    run_dict = asdict(run)
                    entry = case_results[case.template]
                    entry["runs"].append(run_dict)
                    if run.issues:
                        entry["has_issues"] = True

                public_context.close()
                auth_context.close()

            results = list(case_results.values())

            browser.close()
    finally:
        server.shutdown()
        server.join(timeout=5)

    summary, report_markdown = generate_report(results, skipped)
    REPORT_PATH.write_text(report_markdown, encoding="utf-8")
    JSON_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(report_markdown)
    print(f"Markdown report: {REPORT_PATH}")
    print(f"JSON report: {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
