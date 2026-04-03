import re
from datetime import date
from io import BytesIO

import pytest
from openpyxl import Workbook

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    GLCode,
    Invoice,
    InvoiceProduct,
    Item,
    Location,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    Setting,
    User,
    TerminalSaleProductAlias,
    Vendor,
)
from app.routes.report_routes import (
    _auto_resolve_department_products,
    _calculate_department_usage,
    _collect_department_product_totals,
    _department_sales_serializer,
    _CREATE_SELECTION_VALUE,
    _SKIP_SELECTION_VALUE,
    _merge_product_mappings,
)
from app.utils.pos_import import normalize_pos_alias
from app.utils.units import serialize_conversion_setting
from tests.utils import login


def build_department_sales_workbook() -> BytesIO:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Department Sales"

    sheet.append(
        [
            "Unit Price inc",
            "Unit Tax",
            "Quantity",
            "Net ex",
            "Tax",
            "Net inc",
            "Discounts",
            "Gross ex",
            None,
            None,
            None,
            "Amount",
            "%",
            None,
            None,
        ]
    )
    sheet.append(
        [
            "401000 Sample Beverages",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ]
    )
    sheet.append(
        [
            "2001",
            "Sample Soda",
            2.5,
            0.25,
            10,
            22.5,
            2.5,
            25,
            0,
            22.5,
            None,
            None,
            0,
            22.5,
            100,
        ]
    )
    sheet.append(
        [
            "2002",
            "Sample Water",
            1.5,
            0.15,
            5,
            6.75,
            0.75,
            7.5,
            0,
            6.75,
            None,
            None,
            0,
            6.75,
            100,
        ]
    )
    sheet.append(
        [
            None,
            15,
            29.25,
            3.25,
            32.5,
            0,
            29.25,
            None,
            None,
            0,
            29.25,
            100,
            None,
            None,
            None,
        ]
    )
    sheet.append([None] * 15)
    sheet.append(
        [
            "401001 Sample Snacks",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ]
    )
    sheet.append(
        [
            "3001",
            "Sample Chips",
            3,
            0.3,
            4,
            10.8,
            1.2,
            12,
            0,
            10.8,
            None,
            None,
            0,
            10.8,
            100,
        ]
    )
    sheet.append(
        [
            "3002",
            "Sample Candy",
            2,
            0.2,
            3,
            5.4,
            0.6,
            6,
            0,
            5.4,
            None,
            None,
            0,
            5.4,
            100,
        ]
    )
    sheet.append(
        [
            None,
            7,
            16.2,
            1.8,
            18,
            0,
            16.2,
            None,
            None,
            0,
            16.2,
            100,
            None,
            None,
            None,
        ]
    )
    sheet.append([None] * 15)
    sheet.append(
        [
            "DONUTS",
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ]
    )
    sheet.append(
        [
            "4001",
            "Glazed Donut",
            4,
            0.4,
            6,
            21.6,
            2.4,
            24,
            0,
            21.6,
            None,
            None,
            0,
            21.6,
            100,
        ]
    )
    sheet.append(
        [
            None,
            6,
            21.6,
            2.4,
            24,
            0,
            21.6,
            None,
            None,
            0,
            21.6,
            100,
            None,
            None,
            None,
        ]
    )

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def setup_invoice(app):
    with app.app_context():
        user = User.query.filter_by(email="report@example.com").first()
        if not user:
            user = User(
                email="report@example.com",
                password=generate_password_hash("pass"),
                active=True,
            )
            db.session.add(user)

        customer = Customer.query.filter_by(first_name="Jane", last_name="Doe").first()
        if not customer:
            customer = Customer(first_name="Jane", last_name="Doe")
            db.session.add(customer)

        sales_gl_4000 = GLCode.query.filter_by(code="4000").first()
        if not sales_gl_4000:
            sales_gl_4000 = GLCode(code="4000", description="Food Sales")
            db.session.add(sales_gl_4000)

        sales_gl_4010 = GLCode.query.filter_by(code="4010").first()
        if not sales_gl_4010:
            sales_gl_4010 = GLCode(code="4010", description="Beverage Sales")
            db.session.add(sales_gl_4010)

        product = Product.query.filter_by(name="Widget").first()
        if not product:
            product = Product(
                name="Widget",
                price=10.0,
                cost=5.0,
                sales_gl_code=sales_gl_4000,
            )
            db.session.add(product)
        else:
            product.sales_gl_code = sales_gl_4000

        second_product = Product.query.filter_by(name="Gadget").first()
        if not second_product:
            second_product = Product(
                name="Gadget",
                price=8.0,
                cost=3.0,
                sales_gl_code=sales_gl_4010,
            )
            db.session.add(second_product)
        else:
            second_product.sales_gl_code = sales_gl_4010

        db.session.commit()
        invoice = Invoice.query.get("INVREP001")
        if not invoice:
            invoice = Invoice(
                id="INVREP001",
                user_id=user.id,
                customer_id=customer.id,
                date_created=date(2023, 1, 1),
            )
            db.session.add(invoice)
            db.session.commit()

        has_widget = (
            InvoiceProduct.query.filter_by(invoice_id=invoice.id, product_id=product.id)
            .first()
            is not None
        )
        if not has_widget:
            db.session.add(
                InvoiceProduct(
                    invoice_id=invoice.id,
                    quantity=2,
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=product.price,
                    line_subtotal=20,
                    line_gst=0,
                    line_pst=0,
                )
            )
            db.session.commit()

        has_gadget = (
            InvoiceProduct.query.filter_by(
                invoice_id=invoice.id, product_id=second_product.id
            )
            .first()
            is not None
        )
        if not has_gadget:
            db.session.add(
                InvoiceProduct(
                    invoice_id=invoice.id,
                    quantity=1,
                    product_id=second_product.id,
                    product_name=second_product.name,
                    unit_price=second_product.price,
                    line_subtotal=8,
                    line_gst=0,
                    line_pst=0,
                )
            )
            db.session.commit()
        return customer.id


def setup_purchase_invoice(app):
    with app.app_context():
        user = User.query.filter_by(email="purchasereport@example.com").first()
        if not user:
            user = User(
                email="purchasereport@example.com",
                password=generate_password_hash("pass"),
                active=True,
            )
            db.session.add(user)

        vendor = Vendor.query.filter_by(first_name="Report", last_name="Vendor").first()
        if not vendor:
            vendor = Vendor(first_name="Report", last_name="Vendor")
            db.session.add(vendor)

        location = Location.query.filter_by(name="Report Location").first()
        if not location:
            location = Location(name="Report Location")
            db.session.add(location)

        item = Item.query.filter_by(name="Purchase Widget").first()
        if not item:
            item = Item(name="Purchase Widget", base_unit="each", cost=3.0)
            db.session.add(item)

        db.session.commit()

        po = PurchaseOrder.query.filter_by(
            vendor_id=vendor.id,
            user_id=user.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
        ).first()
        if not po:
            po = PurchaseOrder(
                vendor_id=vendor.id,
                user_id=user.id,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                order_date=date(2023, 1, 1),
                expected_date=date(2023, 1, 1),
            )
            db.session.add(po)
            db.session.commit()

        invoice = PurchaseInvoice.query.filter_by(
            invoice_number="PINVREP001"
        ).first()
        if not invoice:
            invoice = PurchaseInvoice(
                purchase_order_id=po.id,
                user_id=user.id,
                location_id=location.id,
                location_name=location.name,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                received_date=date(2023, 1, 15),
                invoice_number="PINVREP001",
                gst=0,
                pst=0,
                delivery_charge=0,
            )
            db.session.add(invoice)
            db.session.commit()

        line_exists = (
            PurchaseInvoiceItem.query.filter_by(
                invoice_id=invoice.id, item_id=item.id
            ).first()
            is not None
        )
        if not line_exists:
            db.session.add(
                PurchaseInvoiceItem(
                    invoice_id=invoice.id,
                    item_id=item.id,
                    item_name=item.name,
                    quantity=5,
                    cost=3.0,
                )
            )
            db.session.commit()

        return user.email, invoice.received_date, item.name


def setup_department_sales_forecast_data(app):
    with app.app_context():
        soda_item = Item(name="Soda Syrup", base_unit="each", cost=2.0)
        water_item = Item(name="Water Bottle", base_unit="each", cost=0.5)
        snack_item = Item(name="Snack Bag", base_unit="each", cost=1.5)
        candy_item = Item(name="Candy Bulk", base_unit="each", cost=0.75)
        db.session.add_all([soda_item, water_item, snack_item, candy_item])
        db.session.flush()

        soda_product = Product(name="Sample Soda", price=3.0, cost=2.0)
        water_product = Product(name="Sample Water", price=2.5, cost=1.0)
        chips_product = Product(name="Sample Chips", price=4.0, cost=1.5)
        candy_product = Product(name="Candy Delight", price=3.0, cost=1.2)
        db.session.add_all([soda_product, water_product, chips_product, candy_product])
        db.session.flush()

        db.session.add_all(
            [
                ProductRecipeItem(
                    product_id=soda_product.id, item_id=soda_item.id, quantity=1.0
                ),
                ProductRecipeItem(
                    product_id=water_product.id, item_id=water_item.id, quantity=1.0
                ),
                ProductRecipeItem(
                    product_id=chips_product.id, item_id=snack_item.id, quantity=1.0
                ),
                ProductRecipeItem(
                    product_id=candy_product.id, item_id=candy_item.id, quantity=1.0
                ),
            ]
        )

        db.session.add(
            TerminalSaleProductAlias(
                source_name="Sample Soda",
                normalized_name=normalize_pos_alias("Sample Soda"),
                product_id=soda_product.id,
            )
        )

        db.session.commit()

        return {
            "soda": soda_product.id,
            "water": water_product.id,
            "chips": chips_product.id,
            "candy": candy_product.id,
        }


def setup_purchase_invoice_with_gl_allocations(app):
    with app.app_context():
        user = User.query.filter_by(email="glreport@example.com").first()
        if not user:
            user = User(
                email="glreport@example.com",
                password=generate_password_hash("pass"),
                active=True,
            )
            db.session.add(user)

        vendor = Vendor.query.filter_by(first_name="GL", last_name="Vendor").first()
        if not vendor:
            vendor = Vendor(first_name="GL", last_name="Vendor")
            db.session.add(vendor)

        location = Location.query.filter_by(name="GL Report Location").first()
        if not location:
            location = Location(name="GL Report Location")
            db.session.add(location)

        gl_food = GLCode.query.filter_by(code="5000").first()
        if not gl_food:
            gl_food = GLCode(code="5000", description="Food Expense")
            db.session.add(gl_food)

        gl_supplies = GLCode.query.filter_by(code="6000").first()
        if not gl_supplies:
            gl_supplies = GLCode(code="6000", description="Supplies Expense")
            db.session.add(gl_supplies)

        gst_gl = GLCode.query.filter_by(code="102702").first()
        if not gst_gl:
            gst_gl = GLCode(code="102702", description="GST Payable")
            db.session.add(gst_gl)

        db.session.commit()

        food_item = Item.query.filter_by(name="GL Food Item").first()
        if not food_item:
            food_item = Item(
                name="GL Food Item",
                base_unit="each",
                cost=2.0,
                purchase_gl_code=gl_food,
            )
            db.session.add(food_item)
        else:
            food_item.purchase_gl_code = gl_food

        supply_item = Item.query.filter_by(name="GL Supply Item").first()
        if not supply_item:
            supply_item = Item(
                name="GL Supply Item",
                base_unit="each",
                cost=4.0,
                purchase_gl_code=gl_supplies,
            )
            db.session.add(supply_item)
        else:
            supply_item.purchase_gl_code = gl_supplies

        db.session.commit()

        po = PurchaseOrder.query.filter_by(
            vendor_id=vendor.id,
            user_id=user.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
        ).first()
        if not po:
            po = PurchaseOrder(
                vendor_id=vendor.id,
                user_id=user.id,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                order_date=date(2023, 2, 1),
                expected_date=date(2023, 2, 1),
            )
            db.session.add(po)
            db.session.commit()

        invoice = PurchaseInvoice.query.filter_by(invoice_number="PINVGL001").first()
        if not invoice:
            invoice = PurchaseInvoice(
                purchase_order_id=po.id,
                user_id=user.id,
                location_id=location.id,
                location_name=location.name,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                received_date=date(2023, 2, 5),
                invoice_number="PINVGL001",
                gst=5.00,
                pst=7.50,
                delivery_charge=10.00,
            )
            db.session.add(invoice)
            db.session.commit()

        if (
            PurchaseInvoiceItem.query.filter_by(
                invoice_id=invoice.id, item_id=food_item.id
            ).first()
            is None
        ):
            db.session.add(
                PurchaseInvoiceItem(
                    invoice_id=invoice.id,
                    item_id=food_item.id,
                    item_name=food_item.name,
                    quantity=10,
                    cost=2.0,
                    purchase_gl_code=gl_food,
                )
            )

        if (
            PurchaseInvoiceItem.query.filter_by(
                invoice_id=invoice.id, item_id=supply_item.id
            ).first()
            is None
        ):
            db.session.add(
                PurchaseInvoiceItem(
                    invoice_id=invoice.id,
                    item_id=supply_item.id,
                    item_name=supply_item.name,
                    quantity=5,
                    cost=4.0,
                    purchase_gl_code=gl_supplies,
                )
            )

        db.session.commit()

        return user.email, invoice.id


def test_purchase_inventory_summary_converts_units(client, app):
    with app.app_context():
        admin_user = User.query.filter_by(email="admin@example.com").first()
        if not admin_user:
            admin_user = User(
                email="admin@example.com",
                password=generate_password_hash("adminpass"),
                active=True,
                is_admin=True,
            )
            db.session.add(admin_user)
            db.session.commit()
        vendor = Vendor(first_name="Convert", last_name="Vendor")
        location = Location(name="Convert Location")
        item = Item(name="Converted Item", base_unit="gram", cost=0.5)
        db.session.add_all([vendor, location, item])
        db.session.flush()

        po = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=admin_user.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
            order_date=date(2024, 1, 1),
            expected_date=date(2024, 1, 2),
        )
        db.session.add(po)
        db.session.flush()

        invoice = PurchaseInvoice(
            purchase_order_id=po.id,
            user_id=admin_user.id,
            location_id=location.id,
            location_name=location.name,
            vendor_name=f"{vendor.first_name} {vendor.last_name}",
            received_date=date(2024, 1, 15),
            invoice_number="CONV001",
            gst=0,
            pst=0,
            delivery_charge=0,
        )
        db.session.add(invoice)
        db.session.flush()

        db.session.add(
            PurchaseInvoiceItem(
                invoice_id=invoice.id,
                item_id=item.id,
                item_name=item.name,
                quantity=1000,
                cost=0.5,
            )
        )

        setting = Setting.query.filter_by(name="BASE_UNIT_CONVERSIONS").first()
        mapping = {
            "ounce": "ounce",
            "gram": "ounce",
            "each": "each",
            "millilitre": "millilitre",
        }
        setting.value = serialize_conversion_setting(mapping)
        db.session.commit()
        app.config["BASE_UNIT_CONVERSIONS"] = mapping

    login(client, "admin@example.com", "adminpass")
    response = client.post(
        "/reports/purchase-inventory-summary",
        data={
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Converted Item" in response.data
    assert b"35.27" in response.data
    assert b"Ounce" in response.data


def test_vendor_and_sales_reports(client, app):
    cid = setup_invoice(app)
    login(client, "report@example.com", "pass")
    resp = client.get("/reports/vendor-invoices")
    assert resp.status_code == 200
    assert b"Hold Ctrl to select multiple customers" in resp.data
    assert b"<select" in resp.data
    assert b"multiple" in resp.data
    resp = client.post(
        "/reports/vendor-invoices",
        data={
            "customer": [str(cid)],
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"INVREP001" in resp.data
    resp = client.get("/reports/product-sales")
    assert resp.status_code == 200
    resp = client.post(
        "/reports/product-sales",
        data={"start_date": "2022-12-31", "end_date": "2023-12-31"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Widget" in resp.data
    assert b"Gadget" in resp.data

    with app.app_context():
        widget = Product.query.filter_by(name="Widget").first()
        widget_gl = widget.sales_gl_code_id

    resp = client.post(
        "/reports/product-sales",
        data={
            "start_date": "2022-12-31",
            "end_date": "2023-12-31",
            "products": [str(widget.id)],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Widget" in resp.data
    assert b"Gadget" not in resp.data

    resp = client.post(
        "/reports/product-sales",
        data={
            "start_date": "2022-12-31",
            "end_date": "2023-12-31",
            "gl_codes": [str(widget_gl)],
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Widget" in resp.data
    assert b"Gadget" not in resp.data


def test_vendor_invoice_report_groups_results_by_customer_for_multi_select(client, app):
    first_customer_id = setup_invoice(app)
    login(client, "report@example.com", "pass")

    with app.app_context():
        user = User.query.filter_by(email="report@example.com").first()
        second_customer = Customer(first_name="Alice", last_name="Zephyr")
        db.session.add(second_customer)
        db.session.flush()

        second_invoice = Invoice(
            id="INVREP003",
            user_id=user.id,
            customer_id=second_customer.id,
            date_created=date(2023, 3, 1),
            is_paid=True,
            paid_at=date(2023, 3, 2),
        )
        db.session.add(second_invoice)
        db.session.flush()

        db.session.add(
            InvoiceProduct(
                invoice_id=second_invoice.id,
                quantity=1,
                product_id=None,
                product_name="Grouped Row",
                unit_price=15.0,
                line_subtotal=15.0,
                line_gst=0.0,
                line_pst=0.0,
            )
        )
        db.session.commit()
        second_customer_id = second_customer.id

    response = client.get(
        "/reports/vendor-invoices/results",
        query_string={
            "customer_ids": f"{first_customer_id},{second_customer_id}",
            "start": "2023-01-01",
            "end": "2023-12-31",
            "payment_status": "all",
        },
    )

    assert response.status_code == 200
    assert b"Customer</th>" in response.data
    assert b"Jane Doe" in response.data
    assert b"Alice Zephyr" in response.data
    assert b"INVREP001" in response.data
    assert b"INVREP003" in response.data
    assert b'<tr class="table-secondary">' in response.data


def test_vendor_invoice_report_uses_line_subtotals_not_live_product_price(client, app):
    customer_id = setup_invoice(app)
    login(client, "report@example.com", "pass")

    with app.app_context():
        product = Product.query.filter_by(name="Widget").first()
        product.price = 999.0
        db.session.commit()

    response = client.get(
        "/reports/vendor-invoices/results",
        query_string={
            "customer_ids": str(customer_id),
            "start": "2023-01-01",
            "end": "2023-12-31",
        },
    )

    assert response.status_code == 200
    assert b"INVREP001" in response.data
    assert b"$31.36" in response.data


def test_vendor_invoice_report_handles_null_product_rows_with_warning(
    client, app, caplog
):
    customer_id = setup_invoice(app)
    login(client, "report@example.com", "pass")

    with app.app_context():
        invoice = Invoice.query.get("INVREP001")
        orphan_row = InvoiceProduct(
            invoice_id=invoice.id,
            quantity=3,
            product_id=None,
            product_name="Orphaned Row",
            unit_price=4.0,
            line_subtotal=11.0,
            line_gst=0.0,
            line_pst=0.0,
        )
        db.session.add(orphan_row)
        db.session.commit()
        orphan_id = orphan_row.id

    with caplog.at_level("WARNING"):
        response = client.get(
            "/reports/vendor-invoices/results",
            query_string={
                "customer_ids": str(customer_id),
                "start": "2023-01-01",
                "end": "2023-12-31",
            },
        )

    assert response.status_code == 200
    assert b"$43.68" in response.data
    assert any(
        "invoice_id=INVREP001" in record.message
        and f"invoice_product_id={orphan_id}" in record.message
        for record in caplog.records
    )


def test_vendor_invoice_report_mixed_invoices_include_linked_and_orphan_rows(client, app):
    customer_id = setup_invoice(app)
    login(client, "report@example.com", "pass")

    with app.app_context():
        user = User.query.filter_by(email="report@example.com").first()
        customer = Customer.query.get(customer_id)
        gadget = Product.query.filter_by(name="Gadget").first()
        invoice = Invoice(
            id="INVREP002",
            user_id=user.id,
            customer_id=customer.id,
            date_created=date(2023, 2, 1),
        )
        db.session.add(invoice)
        db.session.flush()
        db.session.add(
            InvoiceProduct(
                invoice_id=invoice.id,
                quantity=2,
                product_id=gadget.id,
                product_name=gadget.name,
                unit_price=8.0,
                line_subtotal=16.0,
                line_gst=0.0,
                line_pst=0.0,
            )
        )
        db.session.add(
            InvoiceProduct(
                invoice_id=invoice.id,
                quantity=1,
                product_id=None,
                product_name="Legacy Row",
                unit_price=2.0,
                line_subtotal=2.0,
                line_gst=0.0,
                line_pst=0.0,
            )
        )
        db.session.commit()

    response = client.get(
        "/reports/vendor-invoices/results",
        query_string={
            "customer_ids": str(customer_id),
            "start": "2023-01-01",
            "end": "2023-12-31",
        },
    )
    assert response.status_code == 200
    assert b"INVREP001" in response.data
    assert b"INVREP002" in response.data
    assert b"$20.16" in response.data
    assert b"$51.52" in response.data


def test_vendor_invoice_report_payment_status_paid_filters_results(client, app):
    customer_id = setup_invoice(app)
    login(client, "report@example.com", "pass")

    with app.app_context():
        user = User.query.filter_by(email="report@example.com").first()
        customer = Customer.query.get(customer_id)

        paid_invoice = Invoice.query.get("INVREPPAID")
        if not paid_invoice:
            paid_invoice = Invoice(
                id="INVREPPAID",
                user_id=user.id,
                customer_id=customer.id,
                date_created=date(2023, 6, 15),
                is_paid=True,
                paid_at=date(2023, 6, 20),
            )
            db.session.add(paid_invoice)
        else:
            paid_invoice.date_created = date(2023, 6, 15)
            paid_invoice.is_paid = True
            paid_invoice.paid_at = date(2023, 6, 20)

        unpaid_invoice = Invoice.query.get("INVREPUNPAID")
        if not unpaid_invoice:
            unpaid_invoice = Invoice(
                id="INVREPUNPAID",
                user_id=user.id,
                customer_id=customer.id,
                date_created=date(2023, 6, 10),
                is_paid=False,
                paid_at=None,
            )
            db.session.add(unpaid_invoice)
        else:
            unpaid_invoice.date_created = date(2023, 6, 10)
            unpaid_invoice.is_paid = False
            unpaid_invoice.paid_at = None

        out_of_range_paid = Invoice.query.get("INVREPPAIDOLD")
        if not out_of_range_paid:
            out_of_range_paid = Invoice(
                id="INVREPPAIDOLD",
                user_id=user.id,
                customer_id=customer.id,
                date_created=date(2022, 12, 31),
                is_paid=True,
                paid_at=date(2022, 12, 31),
            )
            db.session.add(out_of_range_paid)
        else:
            out_of_range_paid.date_created = date(2022, 12, 31)
            out_of_range_paid.is_paid = True
            out_of_range_paid.paid_at = date(2022, 12, 31)

        db.session.commit()

    response = client.get(
        "/reports/vendor-invoices/results",
        query_string={
            "customer_ids": str(customer_id),
            "start": "2023-01-01",
            "end": "2023-12-31",
            "payment_status": "paid",
        },
    )
    assert response.status_code == 200
    assert b"INVREPPAID" in response.data
    assert b"INVREPUNPAID" not in response.data
    assert b"INVREPPAIDOLD" not in response.data
    assert b"Status:</strong>" in response.data
    assert b"Paid" in response.data


def test_vendor_invoice_report_payment_status_unpaid_filters_results(client, app):
    customer_id = setup_invoice(app)
    login(client, "report@example.com", "pass")

    with app.app_context():
        invoice = Invoice.query.get("INVREP001")
        invoice.date_created = date(2023, 1, 10)
        invoice.is_paid = False
        invoice.paid_at = None

        paid_invoice = Invoice.query.get("INVREPPAID2")
        if not paid_invoice:
            user = User.query.filter_by(email="report@example.com").first()
            paid_invoice = Invoice(
                id="INVREPPAID2",
                user_id=user.id,
                customer_id=invoice.customer_id,
                date_created=date(2023, 1, 11),
                is_paid=True,
                paid_at=date(2023, 1, 12),
            )
            db.session.add(paid_invoice)
        else:
            paid_invoice.date_created = date(2023, 1, 11)
            paid_invoice.is_paid = True
            paid_invoice.paid_at = date(2023, 1, 12)

        db.session.commit()

    response = client.get(
        "/reports/vendor-invoices/results",
        query_string={
            "customer_ids": str(customer_id),
            "start": "2023-01-01",
            "end": "2023-12-31",
            "payment_status": "unpaid",
        },
    )
    assert response.status_code == 200
    assert b"INVREP001" in response.data
    assert b"INVREPPAID2" not in response.data
    assert b"Unpaid" in response.data


def test_vendor_invoice_report_payment_status_all_shows_mixed_status_column(client, app):
    customer_id = setup_invoice(app)
    login(client, "report@example.com", "pass")

    with app.app_context():
        base_invoice = Invoice.query.get("INVREP001")
        base_invoice.is_paid = False
        base_invoice.paid_at = None
        base_invoice.date_created = date(2023, 2, 1)

        paid_invoice = Invoice.query.get("INVREPMIXEDPAID")
        if not paid_invoice:
            user = User.query.filter_by(email="report@example.com").first()
            paid_invoice = Invoice(
                id="INVREPMIXEDPAID",
                user_id=user.id,
                customer_id=base_invoice.customer_id,
                date_created=date(2023, 2, 2),
                is_paid=True,
                paid_at=date(2023, 2, 3),
            )
            db.session.add(paid_invoice)
        else:
            paid_invoice.is_paid = True
            paid_invoice.paid_at = date(2023, 2, 3)
            paid_invoice.date_created = date(2023, 2, 2)

        db.session.commit()

    response = client.get(
        "/reports/vendor-invoices/results",
        query_string={
            "customer_ids": str(customer_id),
            "start": "2023-01-01",
            "end": "2023-12-31",
            "payment_status": "all",
        },
    )
    assert response.status_code == 200
    assert b"INVREP001" in response.data
    assert b"INVREPMIXEDPAID" in response.data
    assert b"Payment Status" in response.data
    assert b"Paid" in response.data
    assert b"Unpaid" in response.data


def test_purchase_cost_forecast_report(client, app):
    setup_invoice(app)
    login(client, "report@example.com", "pass")

    resp = client.get("/reports/purchase-cost-forecast")
    assert resp.status_code == 200

    resp = client.post(
        "/reports/purchase-cost-forecast",
        data={
            "forecast_period": "7",
            "location_id": "0",
            "purchase_gl_code_ids": ["0"],
            "item_id": "0",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"No forecast data was available" in resp.data


def test_purchase_inventory_summary_report(client, app):
    email, received_date, item_name = setup_purchase_invoice(app)
    login(client, email, "pass")

    resp = client.get("/reports/purchase-inventory-summary")
    assert resp.status_code == 200

    resp = client.post(
        "/reports/purchase-inventory-summary",
        data={
            "start_date": "2023-01-01",
            "end_date": "2023-12-31",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert b"Purchase Inventory Summary" in resp.data
    assert item_name.encode() in resp.data
    assert b"$15.00" in resp.data


def test_inventory_variance_report(client, app):
    with app.app_context():
        password = "variance"
        user = User.query.filter_by(email="variance@example.com").first()
        if not user:
            user = User(
                email="variance@example.com",
                password=generate_password_hash(password),
                active=True,
                is_admin=True,
            )
            db.session.add(user)
        else:
            user.password = generate_password_hash(password)

        vendor = Vendor.query.filter_by(first_name="Variance", last_name="Vendor").first()
        if not vendor:
            vendor = Vendor(first_name="Variance", last_name="Vendor")
            db.session.add(vendor)

        location = Location.query.filter_by(name="Variance Location").first()
        if not location:
            location = Location(name="Variance Location")
            db.session.add(location)

        customer = Customer.query.filter_by(first_name="Variance", last_name="Customer").first()
        if not customer:
            customer = Customer(first_name="Variance", last_name="Customer")
            db.session.add(customer)

        gl_code = GLCode.query.filter_by(code="5000").first()
        if not gl_code:
            gl_code = GLCode(code="5000", description="Food Supplies")
            db.session.add(gl_code)

        item = Item.query.filter_by(name="Variance Ingredient").first()
        if not item:
            item = Item(name="Variance Ingredient", base_unit="each", cost=3.0)
            db.session.add(item)

        product = Product.query.filter_by(name="Variance Meal").first()
        if not product:
            product = Product(name="Variance Meal", price=12.0, cost=6.0)
            db.session.add(product)

        db.session.commit()

        if not ProductRecipeItem.query.filter_by(product_id=product.id, item_id=item.id).first():
            db.session.add(
                ProductRecipeItem(
                    product_id=product.id,
                    item_id=item.id,
                    quantity=2.0,
                )
            )
            db.session.commit()

        purchase_order = PurchaseOrder.query.filter_by(vendor_id=vendor.id).first()
        if not purchase_order:
            purchase_order = PurchaseOrder(
                vendor_id=vendor.id,
                user_id=user.id,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                order_date=date(2024, 2, 1),
                expected_date=date(2024, 2, 2),
            )
            db.session.add(purchase_order)
            db.session.commit()

        invoice = (
            PurchaseInvoice.query.filter_by(purchase_order_id=purchase_order.id)
            .filter_by(invoice_number="VAR-001")
            .first()
        )
        if not invoice:
            invoice = PurchaseInvoice(
                purchase_order_id=purchase_order.id,
                user_id=user.id,
                location_id=location.id,
                location_name=location.name,
                vendor_name=f"{vendor.first_name} {vendor.last_name}",
                received_date=date(2024, 2, 3),
                invoice_number="VAR-001",
                gst=0.0,
                pst=0.0,
                delivery_charge=0.0,
            )
            db.session.add(invoice)
            db.session.commit()

        if (
            PurchaseInvoiceItem.query.filter_by(
                invoice_id=invoice.id, item_id=item.id
            ).first()
            is None
        ):
            db.session.add(
                PurchaseInvoiceItem(
                    invoice_id=invoice.id,
                    item_id=item.id,
                    item_name=item.name,
                    quantity=10,
                    cost=3.0,
                    purchase_gl_code=gl_code,
                )
            )
            db.session.commit()

        invoice_record = Invoice.query.get("VARINV001")
        if not invoice_record:
            invoice_record = Invoice(
                id="VARINV001",
                user_id=user.id,
                customer_id=customer.id,
                date_created=date(2024, 2, 5),
            )
            db.session.add(invoice_record)
            db.session.commit()

        if (
            InvoiceProduct.query.filter_by(
                invoice_id=invoice_record.id, product_id=product.id
            ).first()
            is None
        ):
            db.session.add(
                InvoiceProduct(
                    invoice_id=invoice_record.id,
                    quantity=3,
                    product_id=product.id,
                    product_name=product.name,
                    unit_price=product.price,
                    line_subtotal=product.price * 3,
                    line_gst=0.0,
                    line_pst=0.0,
                )
            )
            db.session.commit()

    login(client, "variance@example.com", password)

    resp = client.get("/reports/inventory-variance")
    assert resp.status_code == 200

    resp = client.post(
        "/reports/inventory-variance",
        data={
            "start_date": "2024-02-01",
            "end_date": "2024-02-29",
        },
        follow_redirects=True,
    )

    assert resp.status_code == 200
    assert b"Inventory Variance Report" in resp.data
    assert b"Variance Ingredient" in resp.data
    assert b"$30.00" in resp.data
    assert b"$18.00" in resp.data
    assert b"$12.00" in resp.data

def test_invoice_gl_code_report(client, app):
    email, invoice_id = setup_purchase_invoice_with_gl_allocations(app)
    login(client, email, "pass")

    resp = client.get(f"/reports/purchase-invoices/{invoice_id}/gl-code")
    assert resp.status_code == 200
    assert b"Invoice GL Code Report" in resp.data
    assert b"5000" in resp.data
    assert b"6000" in resp.data
    assert b"102702" in resp.data
    assert b"$20.00" in resp.data
    assert b"$5.00" in resp.data
    assert b"$3.75" in resp.data
    assert b"$62.50" in resp.data


def test_department_sales_forecast_workflow(client, app):
    product_ids = setup_department_sales_forecast_data(app)
    auto_mapped_keys = set()
    auto_display_lookup = {}

    with app.app_context():
        user = User.query.filter_by(email="forecast@example.com").first()
        if not user:
            user = User(
                email="forecast@example.com",
                password=generate_password_hash("pass"),
                active=True,
            )
            db.session.add(user)
            db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    upload_file = build_department_sales_workbook()
    upload_response = client.post(
        "/reports/department-sales-forecast",
        data={"upload": (upload_file, "department_sales_sample.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert upload_response.status_code == 200
    upload_page = upload_response.get_data(as_text=True)
    state_match = re.search(r'name="state_token" value="([^"]+)"', upload_page)
    assert state_match is not None
    state_token = state_match.group(1)

    with app.app_context():
        serializer = _department_sales_serializer()
        state_data = serializer.loads(state_token)
        payload = state_data["payload"]
        assert len(payload["departments"]) == 3
        assert payload["departments"][0]["gl_code"] == "401000"
        assert payload["departments"][0]["department_name"] == "Sample Beverages"
        options = payload.get("options")
        assert options and options.get("only_mapped") is False

        totals = _collect_department_product_totals(payload)
        expected_keys = {
            normalize_pos_alias("Sample Soda"),
            normalize_pos_alias("Sample Water"),
            normalize_pos_alias("Sample Chips"),
            normalize_pos_alias("Sample Candy"),
            normalize_pos_alias("Glazed Donut"),
        }
        assert set(totals.keys()) == expected_keys

        auto_map = _auto_resolve_department_products(totals)
        auto_display_lookup = {
            totals[normalized]["display_name"]: product_id
            for normalized, product_id in auto_map.items()
        }
        assert auto_map[normalize_pos_alias("Sample Soda")] == product_ids["soda"]
        assert auto_map[normalize_pos_alias("Sample Water")] == product_ids["water"]
        assert auto_map[normalize_pos_alias("Sample Chips")] == product_ids["chips"]
        assert normalize_pos_alias("Sample Candy") not in auto_map

        resolved_map_initial = _merge_product_mappings(
            totals,
            auto_map,
            payload.get("manual_mappings"),
        )
        auto_mapped_keys = {
            normalized
            for normalized, entry in resolved_map_initial.items()
            if entry.get("product_id") and entry.get("status") == "auto"
        }
        sorted_keys = sorted(
            totals.keys(), key=lambda key: totals[key]["display_name"].lower()
        )
        mapping_form = {"state_token": state_token, "only_mapped": "1"}
        candy_key = normalize_pos_alias("Sample Candy")
        donut_key = normalize_pos_alias("Glazed Donut")
        for index, normalized in enumerate(sorted_keys):
            if resolved_map_initial.get(normalized, {}).get("product_id"):
                continue
            mapping_form[f"product-key-{index}"] = normalized
            if normalized == candy_key:
                mapping_form[f"mapping-{index}"] = str(product_ids["candy"])
            elif normalized == donut_key:
                mapping_form[f"mapping-{index}"] = _SKIP_SELECTION_VALUE
            else:
                mapping_form[f"mapping-{index}"] = ""

    mapping_response = client.post(
        "/reports/department-sales-forecast",
        data=mapping_form,
        follow_redirects=True,
    )

    assert mapping_response.status_code == 200
    mapping_page = mapping_response.get_data(as_text=True)
    assert "Product mappings updated." in mapping_page
    assert "Overall Stock Usage" in mapping_page
    product_key_entries = re.findall(
        r'name="product-key-(\d+)" value="([^"]+)"', mapping_page
    )
    product_key_values = [value for _, value in product_key_entries]
    for normalized in auto_mapped_keys:
        assert normalized not in product_key_values
    assert donut_key not in product_key_values
    for display_name, product_id in auto_display_lookup.items():
        assert f"{display_name} (ID {product_id})" in mapping_page
    assert mapping_page.count('badge bg-secondary">Auto</span>') >= len(
        auto_mapped_keys
    )
    assert "No further product mappings are required." in mapping_page
    assert "Skipped products:</strong> Glazed Donut" in mapping_page
    assert "terminal_sales_mapping.js" not in mapping_page

    state_match_updated = re.search(
        r'name="state_token" value="([^"]+)"', mapping_page
    )
    assert state_match_updated is not None
    updated_state_token = state_match_updated.group(1)

    with app.app_context():
        serializer = _department_sales_serializer()
        updated_state = serializer.loads(updated_state_token)
        updated_payload = updated_state["payload"]
        assert updated_payload["options"].get("only_mapped") is True

        manual_mappings = updated_payload.get("manual_mappings") or {}
        candy_key = normalize_pos_alias("Sample Candy")
        donut_key = normalize_pos_alias("Glazed Donut")
        assert manual_mappings[candy_key]["product_id"] == product_ids["candy"]
        assert manual_mappings[candy_key]["status"] == "manual"
        assert manual_mappings[donut_key]["status"] == "skipped"
        assert "product_id" not in manual_mappings[donut_key]

        totals = _collect_department_product_totals(updated_payload)
        resolved_map = _merge_product_mappings(
            totals,
            _auto_resolve_department_products(totals),
            manual_mappings,
        )
        (
            department_reports,
            overall_summary,
            warnings,
            unmapped_products,
            skipped_products,
        ) = _calculate_department_usage(updated_payload, resolved_map, True)

        expected_warning = (
            "Encountered product rows before any department header; those rows were skipped."
        )
        assert warnings == [expected_warning]
        assert skipped_products == ["Glazed Donut"]
        assert unmapped_products == []

        dept_lookup = {dept["department_name"]: dept for dept in department_reports}
        assert set(dept_lookup.keys()) == {"Sample Beverages", "Sample Snacks"}

        beverages = dept_lookup["Sample Beverages"]
        snacks = dept_lookup["Sample Snacks"]

        beverages_items = {
            item["item_name"]: item for item in beverages["items"]
        }
        assert pytest.approx(
            beverages_items["Soda Syrup"]["quantity"], rel=1e-6
        ) == 10.0
        assert pytest.approx(
            beverages_items["Soda Syrup"]["total_cost"], rel=1e-6
        ) == 20.0
        assert pytest.approx(
            beverages_items["Water Bottle"]["quantity"], rel=1e-6
        ) == 5.0
        assert pytest.approx(
            beverages_items["Water Bottle"]["total_cost"], rel=1e-6
        ) == 2.5

        snacks_items = {item["item_name"]: item for item in snacks["items"]}
        assert pytest.approx(snacks_items["Snack Bag"]["quantity"], rel=1e-6) == 4.0
        assert pytest.approx(snacks_items["Snack Bag"]["total_cost"], rel=1e-6) == 6.0
        assert pytest.approx(snacks_items["Candy Bulk"]["quantity"], rel=1e-6) == 3.0
        assert pytest.approx(snacks_items["Candy Bulk"]["total_cost"], rel=1e-6) == 2.25

        assert pytest.approx(overall_summary["total_cost"], rel=1e-6) == 30.75

        alias = TerminalSaleProductAlias.query.filter_by(
            normalized_name=normalize_pos_alias("Sample Candy")
        ).first()
        assert alias is not None
        assert alias.product_id == product_ids["candy"]
        assert alias.source_name == "Sample Candy"


def test_department_sales_forecast_creates_products(client, app):
    setup_department_sales_forecast_data(app)

    with app.app_context():
        user = User.query.filter_by(email="forecast-create@example.com").first()
        if not user:
            user = User(
                email="forecast-create@example.com",
                password=generate_password_hash("pass"),
                active=True,
            )
            db.session.add(user)
            db.session.commit()
        user_id = user.id

    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True

    upload_file = build_department_sales_workbook()
    upload_response = client.post(
        "/reports/department-sales-forecast",
        data={"upload": (upload_file, "department_sales_sample.xlsx")},
        content_type="multipart/form-data",
        follow_redirects=True,
    )

    assert upload_response.status_code == 200
    upload_page = upload_response.get_data(as_text=True)
    state_match = re.search(r'name="state_token" value="([^"]+)"', upload_page)
    assert state_match is not None
    state_token = state_match.group(1)

    with app.app_context():
        serializer = _department_sales_serializer()
        state_data = serializer.loads(state_token)
        payload = state_data["payload"]
        totals = _collect_department_product_totals(payload)
        auto_map = _auto_resolve_department_products(totals)
        resolved_map_initial = _merge_product_mappings(
            totals,
            auto_map,
            payload.get("manual_mappings"),
        )

    sorted_keys = sorted(
        totals.keys(), key=lambda key: totals[key]["display_name"].lower()
    )
    mapping_form = {"state_token": state_token}
    donut_key = normalize_pos_alias("Glazed Donut")
    for index, normalized in enumerate(sorted_keys):
        mapping_form[f"product-key-{index}"] = normalized
        if resolved_map_initial.get(normalized, {}).get("product_id"):
            mapping_form[f"mapping-{index}"] = ""
        elif normalized == donut_key:
            mapping_form[f"mapping-{index}"] = _CREATE_SELECTION_VALUE
        else:
            mapping_form[f"mapping-{index}"] = ""

    create_prompt_response = client.post(
        "/reports/department-sales-forecast",
        data=mapping_form,
        follow_redirects=True,
    )

    assert create_prompt_response.status_code == 200
    prompt_page = create_prompt_response.get_data(as_text=True)
    assert "Create new products" in prompt_page
    assert "Provide details for the new products before continuing." in prompt_page

    quick_form_match = re.search(
        r'name="create-0-csrf_token" value="([^"]+)"', prompt_page
    )
    quick_csrf = quick_form_match.group(1) if quick_form_match else None

    state_match_updated = re.search(
        r'name="state_token" value="([^"]+)"', prompt_page
    )
    assert state_match_updated is not None
    state_token = state_match_updated.group(1)

    creation_form = dict(mapping_form)
    creation_form.update(
        {
            "state_token": state_token,
            "creation-step": "1",
            "create-0-name": "Glazed Donut",
            "create-0-price": "4.00",
            "create-0-cost": "0.00",
            "create-0-sales_gl_code": "0",
            "create-0-recipe_yield_quantity": "1",
            "create-0-recipe_yield_unit": "",
        }
    )
    if quick_csrf:
        creation_form["create-0-csrf_token"] = quick_csrf

    creation_response = client.post(
        "/reports/department-sales-forecast",
        data=creation_form,
        follow_redirects=True,
    )

    assert creation_response.status_code == 200
    creation_page = creation_response.get_data(as_text=True)
    assert "Product mappings updated." in creation_page
    assert "Create new products" not in creation_page

    state_match_final = re.search(
        r'name="state_token" value="([^"]+)"', creation_page
    )
    assert state_match_final is not None
    final_state_token = state_match_final.group(1)

    with app.app_context():
        serializer = _department_sales_serializer()
        final_state = serializer.loads(final_state_token)
        manual_mappings = final_state["payload"].get("manual_mappings") or {}
        assert donut_key in manual_mappings
        new_product_id = manual_mappings[donut_key]["product_id"]
        new_product = db.session.get(Product, new_product_id)
        assert new_product is not None
        assert new_product.name == "Glazed Donut"
        assert float(new_product.price) == pytest.approx(4.0)
        alias = TerminalSaleProductAlias.query.filter_by(
            normalized_name=donut_key
        ).first()
        assert alias is not None
        assert alias.product_id == new_product.id
        assert alias.source_name == "Glazed Donut"
