import re
from datetime import date

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    Invoice,
    InvoiceProduct,
    Item,
    ItemUnit,
    Location,
    Product,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Transfer,
    TransferItem,
    User,
    Vendor,
)
from tests.permission_helpers import grant_permissions
from tests.utils import login


def _strip_scripts(html: str) -> str:
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.IGNORECASE | re.DOTALL)


def _setup_permission_ui_data(app):
    with app.app_context():
        viewer = User(
            email="workflow-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
            is_admin=False,
        )
        purchase_creator = User(
            email="workflow-po-create@example.com",
            password=generate_password_hash("pass"),
            active=True,
            is_admin=False,
        )
        invoice_creator = User(
            email="workflow-invoice-create@example.com",
            password=generate_password_hash("pass"),
            active=True,
            is_admin=False,
        )
        db.session.add_all([viewer, purchase_creator, invoice_creator])
        db.session.flush()

        customer = Customer(first_name="Workflow", last_name="Customer")
        product = Product(name="Workflow Product", price=10.0, cost=4.0, quantity=5.0)
        vendor = Vendor(first_name="Workflow", last_name="Vendor")
        source_location = Location(name="Workflow Source")
        target_location = Location(name="Workflow Target")
        item = Item(name="Workflow Item", base_unit="each", quantity=12.0)
        unit = ItemUnit(
            item=item,
            name="each",
            factor=1.0,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add_all(
            [customer, product, vendor, source_location, target_location, item, unit]
        )
        db.session.flush()

        invoice = Invoice(
            id="WC101",
            user_id=viewer.id,
            customer_id=customer.id,
            status=Invoice.STATUS_PENDING,
            is_paid=False,
        )
        db.session.add(invoice)
        db.session.flush()
        db.session.add(
            InvoiceProduct(
                invoice_id=invoice.id,
                product_id=product.id,
                is_custom_line=False,
                product_name=product.name,
                quantity=1.0,
                unit_price=product.price,
                line_subtotal=product.price,
                line_gst=0.5,
                line_pst=0.7,
                override_gst=None,
                override_pst=None,
            )
        )

        purchase_order = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=viewer.id,
            vendor_name="Workflow Vendor",
            order_number="PO-WORKFLOW-1",
            order_date=date(2026, 4, 1),
            expected_date=date(2026, 4, 2),
            expected_total_cost=18.0,
            delivery_charge=2.0,
            received=False,
        )
        db.session.add(purchase_order)
        db.session.flush()
        db.session.add(
            PurchaseOrderItem(
                purchase_order_id=purchase_order.id,
                item_id=item.id,
                unit_id=unit.id,
                quantity=2.0,
                unit_cost=8.0,
                position=0,
            )
        )

        purchase_invoice = PurchaseInvoice(
            purchase_order_id=purchase_order.id,
            user_id=viewer.id,
            location_id=source_location.id,
            vendor_name="Workflow Vendor",
            location_name=source_location.name,
            received_date=date(2026, 4, 3),
            invoice_number="PINV-WORKFLOW-1",
            gst=0.0,
            pst=0.0,
            delivery_charge=0.0,
        )
        db.session.add(purchase_invoice)
        db.session.flush()
        db.session.add(
            PurchaseInvoiceItem(
                invoice_id=purchase_invoice.id,
                item_id=item.id,
                unit_id=unit.id,
                item_name=item.name,
                unit_name=unit.name,
                quantity=2.0,
                cost=8.0,
                container_deposit=0.0,
                prev_cost=7.5,
                location_id=source_location.id,
                position=0,
            )
        )

        transfer = Transfer(
            from_location_id=source_location.id,
            to_location_id=target_location.id,
            user_id=viewer.id,
            from_location_name=source_location.name,
            to_location_name=target_location.name,
            completed=False,
        )
        db.session.add(transfer)
        db.session.flush()
        db.session.add(
            TransferItem(
                transfer_id=transfer.id,
                item_id=item.id,
                item_name=item.name,
                unit_id=unit.id,
                quantity=3.0,
                completed_quantity=0.0,
            )
        )

        db.session.commit()

        grant_permissions(
            viewer,
            "invoices.view",
            "purchase_orders.view",
            "purchase_invoices.view",
            "transfers.view",
            group_name="Workflow Viewer",
            description="View-only access for workflow UI tests.",
        )
        grant_permissions(
            purchase_creator,
            "purchase_orders.create",
            group_name="Workflow Purchase Creator",
            description="Create purchase orders without item-management helpers.",
        )
        grant_permissions(
            invoice_creator,
            "invoices.create",
            group_name="Workflow Invoice Creator",
            description="Create invoices without customer-create access.",
        )

        return {
            "viewer_email": viewer.email,
            "purchase_creator_email": purchase_creator.email,
            "invoice_creator_email": invoice_creator.email,
            "invoice_id": invoice.id,
            "purchase_order_id": purchase_order.id,
            "purchase_invoice_id": purchase_invoice.id,
            "transfer_id": transfer.id,
        }


def test_view_only_invoice_pages_hide_manage_actions(client, app):
    workflow = _setup_permission_ui_data(app)

    with client:
        login(client, workflow["viewer_email"], "pass")
        response = client.get("/view_invoices", follow_redirects=True)
        assert response.status_code == 200
        html = _strip_scripts(response.get_data(as_text=True))
        assert 'data-bs-target="#createInvoiceModal"' not in html
        assert "/mark-delivered" not in html
        assert "/mark-paid" not in html
        assert "/delete_invoice/" not in html
        assert "Customer Invoice Report" not in html

        detail_response = client.get(
            f"/view_invoice/{workflow['invoice_id']}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        detail_html = _strip_scripts(detail_response.get_data(as_text=True))
        assert "Mark Delivered" not in detail_html
        assert "Mark Paid" not in detail_html


def test_view_only_purchase_order_page_hides_manage_actions(client, app):
    workflow = _setup_permission_ui_data(app)

    with client:
        login(client, workflow["viewer_email"], "pass")
        response = client.get("/purchase_orders", follow_redirects=True)
        assert response.status_code == 200
        html = _strip_scripts(response.get_data(as_text=True))
        assert "/purchase_orders/create" not in html
        assert 'data-bs-target="#uploadPurchaseOrderModal"' not in html
        assert "View Recommendations" not in html
        assert "Merge selected" not in html
        assert "Forecast Purchase Costs" not in html
        assert "/purchase_orders/edit/" not in html
        assert f"/purchase_orders/{workflow['purchase_order_id']}/receive" not in html


def test_purchase_order_create_page_hides_item_management_helpers_without_item_permissions(
    client, app
):
    workflow = _setup_permission_ui_data(app)

    with client:
        login(client, workflow["purchase_creator_email"], "pass")
        response = client.get("/purchase_orders/create", follow_redirects=True)
        assert response.status_code == 200
        html = _strip_scripts(response.get_data(as_text=True))
        assert "Create New Item" not in html
        assert "Edit units" not in html
        assert 'id="newItemModal"' not in html
        assert 'id="manageUnitsModal"' not in html


def test_view_only_purchase_invoice_pages_hide_reverse_and_report_actions(client, app):
    workflow = _setup_permission_ui_data(app)

    with client:
        login(client, workflow["viewer_email"], "pass")
        response = client.get("/purchase_invoices", follow_redirects=True)
        assert response.status_code == 200
        html = _strip_scripts(response.get_data(as_text=True))
        assert "Received Invoice Report" not in html
        assert "Invoice GL Code Report" not in html
        assert "Reverse" not in html

        detail_response = client.get(
            f"/purchase_invoices/{workflow['purchase_invoice_id']}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        detail_html = _strip_scripts(detail_response.get_data(as_text=True))
        assert "Purchase Inventory Summary Report" not in detail_html


def test_view_only_transfer_pages_hide_manage_actions(client, app):
    workflow = _setup_permission_ui_data(app)

    with client:
        login(client, workflow["viewer_email"], "pass")
        response = client.get("/transfers", follow_redirects=True)
        assert response.status_code == 200
        html = _strip_scripts(response.get_data(as_text=True))
        assert 'data-bs-target="#addTransferModal"' not in html
        assert "Generate Report" not in html
        assert "View Last Report" not in html
        assert "edit-transfer-btn" not in html
        assert "/transfers/complete/" not in html
        assert "/transfers/delete/" not in html

        detail_response = client.get(
            f"/transfers/view/{workflow['transfer_id']}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        detail_html = _strip_scripts(detail_response.get_data(as_text=True))
        assert "js-transfer-item-toggle" not in detail_html
        assert "View only" in detail_html


def test_invoice_create_page_hides_customer_create_without_customer_permission(client, app):
    workflow = _setup_permission_ui_data(app)

    with client:
        login(client, workflow["invoice_creator_email"], "pass")
        response = client.get("/create_invoice", follow_redirects=True)
        assert response.status_code == 200
        html = _strip_scripts(response.get_data(as_text=True))
        assert "Create Customer" not in html
        assert 'id="createInvoiceCustomerModal"' not in html
