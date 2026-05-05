import json
import os
from datetime import datetime
import re
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    Invoice,
    InvoiceProduct,
    Item,
    ItemUnit,
    Product,
    ProductRecipeItem,
    User,
)
from app.routes.report_routes import _invoice_product_matches_catalog_product
from tests.utils import login


def setup_sales(app):
    with app.app_context():
        product_name = f"Widget-{uuid4().hex}"
        user = User(
            email=f"sales-{uuid4().hex}@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        customer = Customer(first_name="Jane", last_name="Doe")
        product = Product(name=product_name, price=10.0, cost=5.0, quantity=5)
        db.session.add_all([user, customer, product])
        db.session.commit()
        return user.email, customer.id, product.name, product.id


def setup_sales_without_customer(app):
    with app.app_context():
        product_name = f"Widget-{uuid4().hex}"
        user = User(
            email=f"salesnocustomer-{uuid4().hex}@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        product = Product(name=product_name, price=10.0, cost=5.0, quantity=5)
        db.session.add_all([user, product])
        db.session.commit()
        return user.email, product.name, product.id


def setup_sales_without_product(app):
    with app.app_context():
        user = User(
            email=f"salesnocatalog-{uuid4().hex}@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        customer = Customer(first_name="Service", last_name="Customer")
        db.session.add_all([user, customer])
        db.session.commit()
        return user.email, customer.id


def setup_sales_with_recipe_yield(app):
    with app.app_context():
        product_name = f"Yield Widget-{uuid4().hex}"
        user = User(
            email=f"salesyield-{uuid4().hex}@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        customer = Customer(first_name="Yield", last_name="Customer")
        item = Item(name=f"Yield Item-{uuid4().hex}", base_unit="ounce", quantity=20.0)
        product = Product(
            name=product_name,
            price=10.0,
            cost=5.0,
            quantity=5,
            recipe_yield_quantity=5.0,
        )
        db.session.add_all([user, customer, item, product])
        db.session.flush()

        unit = ItemUnit(
            item_id=item.id,
            name="ounce",
            factor=1.0,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=unit.id,
                quantity=25.0,
                countable=True,
            )
        )
        db.session.commit()
        return user.email, customer.id, product.name, product.id, item.id


def create_sales_invoices(client, email, customer_id, product_name, count):
    with client:
        login(client, email, "pass")
        for _ in range(count):
            create_resp = client.post(
                "/create_invoice",
                data={
                    "customer": float(customer_id),
                    "products": f"{product_name}?1??",
                },
                follow_redirects=True,
            )
            assert create_resp.status_code == 200




def test_create_invoice_handles_integrity_error_with_rollback_and_friendly_message(
    client, app, monkeypatch, caplog
):
    email, cust_id, prod_name, _ = setup_sales(app)

    commit_calls = {"count": 0}
    rollback_calls = {"count": 0}

    original_commit = db.session.commit
    original_rollback = db.session.rollback

    def failing_commit():
        commit_calls["count"] += 1
        if commit_calls["count"] == 1:
            raise IntegrityError(
                "INSERT INTO invoice_product ...",
                {"invoice_id": "generated"},
                Exception("duplicate key"),
            )
        return original_commit()

    def tracking_rollback():
        rollback_calls["count"] += 1
        return original_rollback()

    with client:
        login(client, email, "pass")
        monkeypatch.setattr(db.session, "commit", failing_commit)
        monkeypatch.setattr(db.session, "rollback", tracking_rollback)

        with caplog.at_level("ERROR"):
            response = client.post(
                "/create_invoice",
                data={"customer": float(cust_id), "products": f"{prod_name}?2??"},
                follow_redirects=True,
            )

            assert response.status_code == 200
            assert (
                b"Unable to create invoice right now. Please try again."
                in response.data
            )

            # Session remains usable after rollback; no PendingRollbackError should surface.
            follow_up = client.get("/view_invoices", follow_redirects=True)
            assert follow_up.status_code == 200

    with app.app_context():
        assert Invoice.query.filter_by(customer_id=cust_id).count() == 0

    assert rollback_calls["count"] == 1

def test_sales_invoice_create_view_delete(client, app):
    email, cust_id, prod_name, prod_id = setup_sales(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?2??"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Invoice created successfully" in resp.data

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        assert invoice.status == Invoice.STATUS_PENDING
        assert invoice.invoice_status == Invoice.STATUS_PENDING
        assert invoice.delivered_at is None
        assert invoice.is_paid is False
        assert invoice.paid_at is None
        assert invoice.products[0].quantity == 2
        assert invoice.id.startswith("JD")
        invoice_id = invoice.id
        product = Product.query.get(prod_id)
        assert product.quantity == 3

    with client:
        login(client, email, "pass")
        resp = client.get(f"/view_invoice/{invoice_id}")
        assert resp.status_code == 200
        assert str(invoice_id).encode() in resp.data

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/delete_invoice/{invoice_id}", follow_redirects=True
        )
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Invoice, invoice_id) is None


def test_invoice_survives_product_deletion(client, app):
    email, cust_id, prod_name, prod_id = setup_sales(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?1??"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_id = invoice.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/products/{prod_id}/delete", follow_redirects=True
        )
        assert resp.status_code == 200

    with client:
        login(client, email, "pass")
        resp = client.get(f"/view_invoice/{invoice_id}")
        assert resp.status_code == 200
        assert prod_name.encode() in resp.data


def test_sales_invoice_returns(client, app):
    email, cust_id, prod_name, prod_id = setup_sales(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?-2??"},
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        assert invoice.products[0].quantity == -2
        assert invoice.total == pytest.approx(-22.4)
        product = Product.query.get(prod_id)
        assert product.quantity == 7


def test_sales_invoice_respects_recipe_yield_quantity(client, app):
    email, cust_id, prod_name, prod_id, item_id = setup_sales_with_recipe_yield(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?2??"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Invoice created successfully" in resp.data

    with app.app_context():
        product = Product.query.get(prod_id)
        item = Item.query.get(item_id)

        assert product.quantity == 3
        assert item.quantity == pytest.approx(10.0)


def test_sales_invoice_rejects_empty_invoice_submission(client, app):
    email, cust_id, _, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": ""},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    assert b"Add at least one valid invoice line before creating an invoice." in resp.data

    with app.app_context():
        assert Invoice.query.filter_by(customer_id=cust_id).count() == 0


def test_sales_invoice_api_rejects_empty_invoice_submission(client, app):
    email, cust_id, _, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/api/create_invoice",
            data={"customer": float(cust_id), "products": ""},
        )

    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["errors"]["products"] == [
        "Add at least one valid invoice line before creating an invoice."
    ]

    with app.app_context():
        assert Invoice.query.filter_by(customer_id=cust_id).count() == 0


def test_invoice_pages_include_customer_create_modal(client, app):
    email, _, _, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        create_page = client.get("/create_invoice")
        assert create_page.status_code == 200
        create_html = create_page.get_data(as_text=True)
        assert 'data-bs-target="#createInvoiceCustomerModal"' in create_html
        assert 'id="createInvoiceCustomerModal"' in create_html
        assert 'id="addCustomLineBtn"' in create_html

        list_page = client.get("/view_invoices")
        assert list_page.status_code == 200
        list_html = list_page.get_data(as_text=True)
        assert 'data-bs-target="#createInvoiceCustomerModal"' in list_html
        assert 'id="createInvoiceCustomerModal"' in list_html
        assert 'id="addCustomLineBtn"' in list_html


def test_customer_create_modal_can_feed_invoice_creation_flow(client, app):
    email, product_name, _ = setup_sales_without_customer(app)

    with client:
        login(client, email, "pass")
        customer_resp = client.post(
            "/customers/create-modal",
            data={
                "first_name": "Invoice",
                "last_name": "Customer",
                "gst_exempt": "y",
                "pst_exempt": "",
            },
        )
        assert customer_resp.status_code == 200
        payload = customer_resp.get_json()
        customer_id = payload["customer"]["id"]

        create_resp = client.post(
            "/create_invoice",
            data={
                "customer": float(customer_id),
                "products": f"{product_name}?1??",
            },
            follow_redirects=True,
        )
        assert create_resp.status_code == 200
        assert b"Invoice created successfully" in create_resp.data

    with app.app_context():
        customer = Customer.query.get(customer_id)
        assert customer is not None
        invoice = Invoice.query.filter_by(customer_id=customer_id).first()
        assert invoice is not None
        assert invoice.products[0].product_name == product_name


def test_sales_invoice_supports_custom_lines_without_creating_products(client, app):
    email, cust_id = setup_sales_without_product(app)
    custom_description = "service repair - fixed compressor coil in unit 1"
    custom_lines = json.dumps(
        [
            {
                "line_type": "custom",
                "product_name": custom_description,
                "quantity": 1,
                "unit_price": 125.0,
                "line_gst": 6.25,
                "line_pst": 8.75,
                "additional_fee": 20.0,
            }
        ]
    )

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": custom_lines},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Invoice created successfully" in resp.data

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        assert len(invoice.products) == 1
        invoice_line = invoice.products[0]
        assert invoice_line.product is None
        assert invoice_line.product_id is None
        assert invoice_line.is_custom_line is True
        assert invoice_line.product_name == custom_description
        assert invoice_line.quantity == pytest.approx(1.0)
        assert invoice_line.unit_price == pytest.approx(125.0)
        assert invoice_line.line_subtotal == pytest.approx(145.0)
        assert invoice_line.line_gst == pytest.approx(6.25)
        assert invoice_line.line_pst == pytest.approx(8.75)
        assert invoice.total == pytest.approx(160.0)
        assert Product.query.filter_by(name=custom_description).count() == 0


def test_sales_invoice_api_supports_mixed_catalog_and_custom_lines(client, app):
    email, cust_id, prod_name, prod_id = setup_sales(app)
    invoice_lines = json.dumps(
        [
            {
                "line_type": "catalog",
                "product_name": prod_name,
                "quantity": 2,
                "override_gst": True,
                "override_pst": False,
            },
            {
                "line_type": "custom",
                "product_name": "Emergency labour callout",
                "quantity": 1,
                "unit_price": 50.0,
                "line_gst": 2.5,
                "line_pst": 0.0,
                "additional_fee": 10.0,
            },
        ]
    )

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/api/create_invoice",
            data={"customer": float(cust_id), "products": invoice_lines},
        )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["invoice"]["customer"] == "Jane Doe"
    assert payload["invoice"]["status"] == Invoice.STATUS_PENDING
    assert payload["invoice"]["status_label"] == "Pending"

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        assert len(invoice.products) == 2

        catalog_line = next(
            line for line in invoice.products if line.product_id == prod_id
        )
        custom_line = next(line for line in invoice.products if line.product_id is None)

        assert catalog_line.product_name == prod_name
        assert catalog_line.is_custom_line is False
        assert catalog_line.quantity == pytest.approx(2.0)
        assert custom_line.product_name == "Emergency labour callout"
        assert custom_line.is_custom_line is True
        assert custom_line.unit_price == pytest.approx(50.0)
        assert custom_line.line_subtotal == pytest.approx(60.0)
        assert custom_line.line_gst == pytest.approx(2.5)
        assert custom_line.line_pst == pytest.approx(0.0)

        product = Product.query.get(prod_id)
        assert product.quantity == pytest.approx(3.0)


def test_sales_invoice_rejects_partially_invalid_custom_json_payload(client, app):
    email, cust_id = setup_sales_without_product(app)
    invoice_lines = json.dumps(
        [
            {
                "line_type": "custom",
                "product_name": "Emergency labour callout",
                "quantity": 1,
                "unit_price": 50.0,
                "line_gst": 2.5,
                "line_pst": 0.0,
                "additional_fee": 10.0,
            },
            {
                "line_type": "custom",
                "product_name": "Broken line",
                "quantity": 1,
                "unit_price": "oops",
            },
        ]
    )

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/api/create_invoice",
            data={"customer": float(cust_id), "products": invoice_lines},
        )

    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload["errors"]["products"] == ["Each custom line needs a valid price."]

    with app.app_context():
        assert Invoice.query.filter_by(customer_id=cust_id).count() == 0


def test_custom_invoice_workflow_supports_status_updates_and_delete(client, app):
    email, cust_id = setup_sales_without_product(app)
    custom_description = "service repair - fixed compressor coil in unit 1"
    custom_lines = json.dumps(
        [
            {
                "line_type": "custom",
                "product_name": custom_description,
                "quantity": 1,
                "unit_price": 125.0,
                "line_gst": 6.25,
                "line_pst": 8.75,
                "additional_fee": 20.0,
            }
        ]
    )

    with client:
        login(client, email, "pass")
        create_resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": custom_lines},
            follow_redirects=True,
        )
        assert create_resp.status_code == 200
        assert b"Invoice created successfully" in create_resp.data

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_id = invoice.id
        invoice_line = invoice.products[0]
        assert invoice_line.product_id is None
        assert invoice_line.is_custom_line is True
        assert invoice.status == Invoice.STATUS_PENDING
        assert invoice.delivered_at is None

    with client:
        login(client, email, "pass")
        view_resp = client.get(f"/view_invoice/{invoice_id}")
        assert view_resp.status_code == 200
        view_html = view_resp.get_data(as_text=True)
        assert custom_description in view_html
        assert "$160.00" in view_html

        mark_delivered_resp = client.post(
            f"/invoice/{invoice_id}/mark-delivered", follow_redirects=True
        )
        assert mark_delivered_resp.status_code == 200

        mark_paid_resp = client.post(
            f"/invoice/{invoice_id}/mark-paid", follow_redirects=True
        )
        assert mark_paid_resp.status_code == 200

    with app.app_context():
        paid_invoice = db.session.get(Invoice, invoice_id)
        assert paid_invoice is not None
        assert paid_invoice.status == Invoice.STATUS_PAID
        assert paid_invoice.delivered_at is not None
        assert paid_invoice.is_paid is True
        assert paid_invoice.paid_at is not None

    with client:
        login(client, email, "pass")
        mark_unpaid_resp = client.post(
            f"/invoice/{invoice_id}/mark-unpaid", follow_redirects=True
        )
        assert mark_unpaid_resp.status_code == 200

        delete_resp = client.post(
            f"/delete_invoice/{invoice_id}", follow_redirects=True
        )
        assert delete_resp.status_code == 200
        assert b"Invoice deleted successfully!" in delete_resp.data

    with app.app_context():
        assert db.session.get(Invoice, invoice_id) is None


def test_customer_invoice_report_uses_stored_amounts_for_custom_lines(client, app):
    email, cust_id = setup_sales_without_product(app)
    invoice_lines = json.dumps(
        [
            {
                "line_type": "custom",
                "product_name": "Emergency labour callout",
                "quantity": 1,
                "unit_price": 125.0,
                "line_gst": 6.25,
                "line_pst": 8.75,
                "additional_fee": 20.0,
            }
        ]
    )

    with client:
        login(client, email, "pass")
        create_resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": invoice_lines},
            follow_redirects=True,
        )
        assert create_resp.status_code == 200

    with app.app_context():
        customer = db.session.get(Customer, cust_id)
        customer.gst_exempt = True
        customer.pst_exempt = True
        db.session.commit()

    with client:
        login(client, email, "pass")
        report_resp = client.get(
            (
                "/reports/vendor-invoices/results"
                f"?customer_ids={cust_id}"
                "&start=2000-01-01"
                "&end=2100-01-01"
                "&payment_status=all"
            )
        )

    assert report_resp.status_code == 200
    assert "$160.00" in report_resp.get_data(as_text=True)


def test_custom_invoice_lines_do_not_match_catalog_product_report_joins_when_names_match(
    client, app
):
    email, cust_id, prod_name, prod_id = setup_sales(app)
    invoice_lines = json.dumps(
        [
            {
                "line_type": "catalog",
                "product_name": prod_name,
                "quantity": 1,
                "override_gst": True,
                "override_pst": True,
            },
            {
                "line_type": "custom",
                "product_name": prod_name,
                "quantity": 1,
                "unit_price": 30.0,
                "line_gst": 1.5,
                "line_pst": 2.1,
                "additional_fee": 0.0,
            },
        ]
    )

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/api/create_invoice",
            data={"customer": float(cust_id), "products": invoice_lines},
        )

    assert resp.status_code == 200

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None

        catalog_line = next(
            line
            for line in invoice.products
            if line.product_id == prod_id and not line.is_custom_line
        )
        custom_line = next(
            line
            for line in invoice.products
            if line.product_id is None and line.is_custom_line
        )

        matched_ids = [
            row.id
            for row in db.session.query(InvoiceProduct.id)
            .join(Product, _invoice_product_matches_catalog_product())
            .filter(InvoiceProduct.invoice_id == invoice.id)
            .order_by(InvoiceProduct.id)
            .all()
        ]

        assert catalog_line.id in matched_ids
        assert custom_line.id not in matched_ids


def test_delete_invoice_route_still_accepts_post_from_list_form(client, app):
    email, cust_id, prod_name, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        create_resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?1??"},
            follow_redirects=True,
        )
        assert create_resp.status_code == 200

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_id = invoice.id

    with client:
        login(client, email, "pass")
        list_resp = client.get("/view_invoices", follow_redirects=True)
        assert list_resp.status_code == 200
        html = list_resp.get_data(as_text=True)
        assert f'action="/delete_invoice/{invoice_id}"' in html
        assert 'class="js-confirm-delete-invoice"' in html
        assert 'method="post"' in html

        delete_resp = client.post(
            f"/delete_invoice/{invoice_id}", follow_redirects=True
        )
        assert delete_resp.status_code == 200
        assert b"Invoice deleted successfully!" in delete_resp.data

    with app.app_context():
        assert db.session.get(Invoice, invoice_id) is None


def test_invoice_status_endpoints_enforce_pending_delivered_paid_flow(client, app):
    email, cust_id, prod_name, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        create_resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?1??"},
            follow_redirects=True,
        )
        assert create_resp.status_code == 200

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_id = invoice.id
        assert invoice.status == Invoice.STATUS_PENDING
        assert invoice.delivered_at is None
        assert invoice.is_paid is False
        assert invoice.paid_at is None

    with client:
        login(client, email, "pass")
        mark_paid_resp = client.post(
            f"/invoice/{invoice_id}/mark-paid", follow_redirects=True
        )
        assert mark_paid_resp.status_code == 200
        assert (
            b"must be marked delivered before it can be marked paid"
            in mark_paid_resp.data
        )

    with app.app_context():
        pending_invoice = db.session.get(Invoice, invoice_id)
        assert pending_invoice is not None
        assert pending_invoice.status == Invoice.STATUS_PENDING
        assert pending_invoice.delivered_at is None
        assert pending_invoice.is_paid is False
        assert pending_invoice.paid_at is None

    with client:
        login(client, email, "pass")
        mark_delivered_resp = client.post(
            f"/invoice/{invoice_id}/mark-delivered", follow_redirects=True
        )
        assert mark_delivered_resp.status_code == 200

    with app.app_context():
        delivered_invoice = db.session.get(Invoice, invoice_id)
        assert delivered_invoice is not None
        assert delivered_invoice.status == Invoice.STATUS_DELIVERED
        assert delivered_invoice.delivered_at is not None
        assert delivered_invoice.is_paid is False
        assert delivered_invoice.paid_at is None
        delivered_at = delivered_invoice.delivered_at

    with client:
        login(client, email, "pass")
        mark_paid_resp = client.post(
            f"/invoice/{invoice_id}/mark-paid", follow_redirects=True
        )
        assert mark_paid_resp.status_code == 200

    with app.app_context():
        paid_invoice = db.session.get(Invoice, invoice_id)
        assert paid_invoice is not None
        assert paid_invoice.status == Invoice.STATUS_PAID
        assert paid_invoice.delivered_at == delivered_at
        assert paid_invoice.is_paid is True
        assert paid_invoice.paid_at is not None

    with client:
        login(client, email, "pass")
        mark_unpaid_resp = client.post(
            f"/invoice/{invoice_id}/mark-unpaid", follow_redirects=True
        )
        assert mark_unpaid_resp.status_code == 200

    with app.app_context():
        reopened_invoice = db.session.get(Invoice, invoice_id)
        assert reopened_invoice is not None
        assert reopened_invoice.status == Invoice.STATUS_DELIVERED
        assert reopened_invoice.delivered_at == delivered_at
        assert reopened_invoice.is_paid is False
        assert reopened_invoice.paid_at is None


def test_view_invoices_shows_invoice_status_text(client, app):
    email, cust_id, prod_name, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        create_resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?1??"},
            follow_redirects=True,
        )
        assert create_resp.status_code == 200

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_id = invoice.id

    with client:
        login(client, email, "pass")
        list_resp = client.get("/view_invoices", follow_redirects=True)
        assert list_resp.status_code == 200
        html = list_resp.get_data(as_text=True)
        assert re.search(rf">\s*{invoice_id}\s*<", html)
        assert "badge text-bg-warning" in html
        assert re.search(r">\s*Pending\s*<", html)

        client.post(
            f"/invoice/{invoice_id}/mark-delivered", follow_redirects=True
        )
        delivered_list_resp = client.get("/view_invoices", follow_redirects=True)
        assert delivered_list_resp.status_code == 200
        delivered_html = delivered_list_resp.get_data(as_text=True)
        assert "badge text-bg-info" in delivered_html
        assert re.search(r">\s*Delivered\s*<", delivered_html)

        client.post(f"/invoice/{invoice_id}/mark-paid", follow_redirects=True)
        paid_list_resp = client.get("/view_invoices", follow_redirects=True)
        assert paid_list_resp.status_code == 200
        paid_html = paid_list_resp.get_data(as_text=True)
        assert "badge text-bg-success" in paid_html
        assert re.search(r">\s*Paid\s*<", paid_html)


def test_sales_invoice_uses_invoice_sale_price_for_line_snapshot(client, app):
    email, cust_id, prod_name, prod_id = setup_sales(app)

    with app.app_context():
        product = db.session.get(Product, prod_id)
        product.invoice_sale_price = 12.5
        db.session.commit()

    with client:
        login(client, email, "pass")
        create_resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": f"{prod_name}?2??"},
            follow_redirects=True,
        )
        assert create_resp.status_code == 200

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_line = invoice.products[0]
        assert invoice_line.unit_price == pytest.approx(12.5)
        assert invoice_line.line_subtotal == pytest.approx(25.0)
        assert invoice.total == pytest.approx(28.0)

        product = db.session.get(Product, prod_id)
        product.invoice_sale_price = 50.0
        product.price = 99.0
        db.session.commit()

        refreshed_line = db.session.get(type(invoice_line), invoice_line.id)
        assert refreshed_line.unit_price == pytest.approx(12.5)
        assert refreshed_line.line_subtotal == pytest.approx(25.0)


def test_bulk_invoice_payment_status_updates_selected_invoices(client, app):
    email, cust_id, prod_name, _ = setup_sales(app)
    create_sales_invoices(client, email, cust_id, prod_name, count=3)

    with app.app_context():
        invoices = (
            Invoice.query.filter_by(customer_id=cust_id)
            .order_by(Invoice.date_created.asc())
            .all()
        )
        assert len(invoices) == 3
        invoice_ids = [invoice.id for invoice in invoices]
        target_invoice_ids = invoice_ids[:2]
        assert all(invoice.status == Invoice.STATUS_PENDING for invoice in invoices)
        assert all(invoice.delivered_at is None for invoice in invoices)
        assert all(invoice.is_paid is False for invoice in invoices)
        assert all(invoice.paid_at is None for invoice in invoices)
        untouched_invoice_id = invoice_ids[2]

    with client:
        login(client, email, "pass")
        mark_delivered_response = client.post(
            "/invoices/bulk-payment-status",
            json={"invoice_ids": target_invoice_ids, "status": "delivered"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert mark_delivered_response.status_code == 200
        mark_delivered_payload = mark_delivered_response.get_json()
        assert mark_delivered_payload == {
            "success": True,
            "count": 2,
            "status": "delivered",
            "updated": [
                {
                    "id": target_invoice_ids[0],
                    "status": "delivered",
                    "status_label": "Delivered",
                    "delivered_at": mark_delivered_payload["updated"][0]["delivered_at"],
                    "is_paid": False,
                    "paid_at": None,
                    "payment_status": "Unpaid",
                },
                {
                    "id": target_invoice_ids[1],
                    "status": "delivered",
                    "status_label": "Delivered",
                    "delivered_at": mark_delivered_payload["updated"][1]["delivered_at"],
                    "is_paid": False,
                    "paid_at": None,
                    "payment_status": "Unpaid",
                },
            ],
        }
        assert mark_delivered_payload["updated"][0]["delivered_at"] is not None
        assert mark_delivered_payload["updated"][1]["delivered_at"] is not None

    with app.app_context():
        updated_invoices = Invoice.query.filter(
            Invoice.id.in_(target_invoice_ids)
        ).all()
        assert len(updated_invoices) == 2
        assert all(
            invoice.status == Invoice.STATUS_DELIVERED
            for invoice in updated_invoices
        )
        assert all(invoice.delivered_at is not None for invoice in updated_invoices)
        assert all(invoice.is_paid is False for invoice in updated_invoices)
        assert all(invoice.paid_at is None for invoice in updated_invoices)
        untouched_invoice = db.session.get(Invoice, untouched_invoice_id)
        assert untouched_invoice is not None
        assert untouched_invoice.status == Invoice.STATUS_PENDING
        assert untouched_invoice.delivered_at is None
        assert untouched_invoice.is_paid is False
        assert untouched_invoice.paid_at is None

    with client:
        login(client, email, "pass")
        mark_paid_response = client.post(
            "/invoices/bulk-payment-status",
            json={"invoice_ids": target_invoice_ids, "status": "paid"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert mark_paid_response.status_code == 200
        mark_paid_payload = mark_paid_response.get_json()
        assert mark_paid_payload["success"] is True
        assert mark_paid_payload["count"] == 2
        assert mark_paid_payload["status"] == "paid"
        assert len(mark_paid_payload["updated"]) == 2
        assert all(
            updated["status"] == "paid"
            for updated in mark_paid_payload["updated"]
        )
        assert all(
            updated["status_label"] == "Paid"
            for updated in mark_paid_payload["updated"]
        )
        assert all(
            updated["is_paid"] is True
            for updated in mark_paid_payload["updated"]
        )
        assert all(
            updated["paid_at"] is not None
            for updated in mark_paid_payload["updated"]
        )
        assert all(
            updated["payment_status"] == "Paid"
            for updated in mark_paid_payload["updated"]
        )

    with app.app_context():
        paid_invoices = Invoice.query.filter(Invoice.id.in_(target_invoice_ids)).all()
        assert len(paid_invoices) == 2
        assert all(invoice.status == Invoice.STATUS_PAID for invoice in paid_invoices)
        assert all(invoice.delivered_at is not None for invoice in paid_invoices)
        assert all(invoice.is_paid is True for invoice in paid_invoices)
        assert all(invoice.paid_at is not None for invoice in paid_invoices)

    with client:
        login(client, email, "pass")
        mark_unpaid_response = client.post(
            "/invoices/bulk-payment-status",
            json={"invoice_ids": target_invoice_ids, "is_paid": False},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert mark_unpaid_response.status_code == 200
        mark_unpaid_payload = mark_unpaid_response.get_json()
        assert mark_unpaid_payload["success"] is True
        assert mark_unpaid_payload["count"] == 2
        assert mark_unpaid_payload["status"] == "delivered"
        assert len(mark_unpaid_payload["updated"]) == 2
        assert all(
            updated["status"] == "delivered"
            for updated in mark_unpaid_payload["updated"]
        )
        assert all(
            updated["status_label"] == "Delivered"
            for updated in mark_unpaid_payload["updated"]
        )
        assert all(
            updated["is_paid"] is False
            for updated in mark_unpaid_payload["updated"]
        )
        assert all(
            updated["paid_at"] is None
            for updated in mark_unpaid_payload["updated"]
        )
        assert all(
            updated["payment_status"] == "Unpaid"
            for updated in mark_unpaid_payload["updated"]
        )

    with app.app_context():
        unpaid_invoices = Invoice.query.filter(Invoice.id.in_(target_invoice_ids)).all()
        assert len(unpaid_invoices) == 2
        assert all(
            invoice.status == Invoice.STATUS_DELIVERED
            for invoice in unpaid_invoices
        )
        assert all(invoice.delivered_at is not None for invoice in unpaid_invoices)
        assert all(invoice.is_paid is False for invoice in unpaid_invoices)
        assert all(invoice.paid_at is None for invoice in unpaid_invoices)


def test_bulk_invoice_payment_status_rejects_paid_transition_for_pending_invoices(
    client, app
):
    email, cust_id, prod_name, _ = setup_sales(app)
    create_sales_invoices(client, email, cust_id, prod_name, count=1)

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_id = invoice.id

    with client:
        login(client, email, "pass")
        response = client.post(
            "/invoices/bulk-payment-status",
            json={"invoice_ids": [invoice_id], "status": "paid"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 400
        payload = response.get_json()
        assert payload["success"] is False
        assert payload["message"] == (
            "Pending invoices must be marked delivered before they can be marked paid."
        )
        assert payload["invalid_invoice_ids"] == [invoice_id]

    with app.app_context():
        pending_invoice = db.session.get(Invoice, invoice_id)
        assert pending_invoice is not None
        assert pending_invoice.status == Invoice.STATUS_PENDING
        assert pending_invoice.delivered_at is None
        assert pending_invoice.is_paid is False
        assert pending_invoice.paid_at is None


def test_bulk_invoice_payment_status_rejects_invalid_status(client, app):
    email, cust_id, prod_name, _ = setup_sales(app)
    create_sales_invoices(client, email, cust_id, prod_name, count=1)

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        invoice_id = invoice.id

    with client:
        login(client, email, "pass")
        response = client.post(
            "/invoices/bulk-payment-status",
            json={"invoice_ids": [invoice_id], "is_paid": "sometimes"},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 400
        payload = response.get_json()
        assert payload["success"] is False
        assert payload["message"] == "Select a valid invoice status."


def test_bulk_invoice_payment_status_rejects_empty_selection(client, app):
    email, cust_id, prod_name, _ = setup_sales(app)
    create_sales_invoices(client, email, cust_id, prod_name, count=1)

    with client:
        login(client, email, "pass")
        response = client.post(
            "/invoices/bulk-payment-status",
            json={"invoice_ids": [], "is_paid": True},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 400
        payload = response.get_json()
        assert payload == {
            "success": False,
            "message": "Select at least one invoice.",
        }


def test_bulk_invoice_payment_status_rejects_missing_invoice_ids(client, app):
    email, cust_id, prod_name, _ = setup_sales(app)
    create_sales_invoices(client, email, cust_id, prod_name, count=1)

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        existing_invoice_id = invoice.id

    with client:
        login(client, email, "pass")
        response = client.post(
            "/invoices/bulk-payment-status",
            json={
                "invoice_ids": [existing_invoice_id, "UNKNOWN-INVOICE-ID"],
                "is_paid": True,
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert response.status_code == 404
        payload = response.get_json()
        assert payload["success"] is False
        assert payload["message"] == (
            "Some invoices were not found: UNKNOWN-INVOICE-ID"
        )
        assert payload["missing_invoice_ids"] == ["UNKNOWN-INVOICE-ID"]


def test_view_invoices_rejects_invalid_date_filters(client, app):
    email, _, _, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        resp = client.get(
            "/view_invoices",
            query_string={"start_date": "not-a-date"},
            follow_redirects=True,
        )

    assert resp.status_code == 200
    assert b"Invalid start date." in resp.data


def test_filter_invoices_api_rejects_invalid_date_filters(client, app):
    email, _, _, _ = setup_sales(app)

    with client:
        login(client, email, "pass")
        resp = client.get(
            "/api/filter_invoices",
            query_string={"start_date": "not-a-date"},
        )

    assert resp.status_code == 400
    payload = resp.get_json()
    assert payload == {"errors": {"start_date": ["Invalid start date."]}}
