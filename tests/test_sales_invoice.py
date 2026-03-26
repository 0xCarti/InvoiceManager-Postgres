import os
from datetime import datetime
import re

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import Customer, Invoice, Product, User
from tests.utils import login


def setup_sales(app):
    with app.app_context():
        user = User(
            email="sales@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        customer = Customer(first_name="Jane", last_name="Doe")
        product = Product(name="Widget", price=10.0, cost=5.0, quantity=5)
        db.session.add_all([user, customer, product])
        db.session.commit()
        return user.email, customer.id, product.name, product.id


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


def test_mark_invoice_paid_and_unpaid_endpoints_update_payment_state(client, app):
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
        assert invoice.is_paid is False
        assert invoice.paid_at is None

    with client:
        login(client, email, "pass")
        mark_paid_resp = client.post(
            f"/invoice/{invoice_id}/mark-paid", follow_redirects=True
        )
        assert mark_paid_resp.status_code == 200

    with app.app_context():
        paid_invoice = db.session.get(Invoice, invoice_id)
        assert paid_invoice is not None
        assert paid_invoice.is_paid is True
        assert paid_invoice.paid_at is not None

    with client:
        login(client, email, "pass")
        mark_unpaid_resp = client.post(
            f"/invoice/{invoice_id}/mark-unpaid", follow_redirects=True
        )
        assert mark_unpaid_resp.status_code == 200

    with app.app_context():
        unpaid_invoice = db.session.get(Invoice, invoice_id)
        assert unpaid_invoice is not None
        assert unpaid_invoice.is_paid is False
        assert unpaid_invoice.paid_at is None


def test_view_invoices_shows_payment_status_text(client, app):
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
        assert re.search(r">\s*Unpaid\s*<", html)

    with client:
        login(client, email, "pass")
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
        assert all(invoice.is_paid is False for invoice in invoices)
        assert all(invoice.paid_at is None for invoice in invoices)
        untouched_invoice_id = invoice_ids[2]

    with client:
        login(client, email, "pass")
        mark_paid_response = client.post(
            "/invoices/bulk-payment-status",
            json={"invoice_ids": target_invoice_ids, "is_paid": True},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        assert mark_paid_response.status_code == 200
        mark_paid_payload = mark_paid_response.get_json()
        assert mark_paid_payload == {
            "success": True,
            "count": 2,
            "status": "paid",
            "updated": [
                {
                    "id": target_invoice_ids[0],
                    "is_paid": True,
                    "paid_at": mark_paid_payload["updated"][0]["paid_at"],
                    "payment_status": "Paid",
                },
                {
                    "id": target_invoice_ids[1],
                    "is_paid": True,
                    "paid_at": mark_paid_payload["updated"][1]["paid_at"],
                    "payment_status": "Paid",
                },
            ],
        }
        assert mark_paid_payload["updated"][0]["paid_at"] is not None
        assert mark_paid_payload["updated"][1]["paid_at"] is not None

    with app.app_context():
        updated_invoices = Invoice.query.filter(
            Invoice.id.in_(target_invoice_ids)
        ).all()
        assert len(updated_invoices) == 2
        assert all(invoice.is_paid is True for invoice in updated_invoices)
        assert all(invoice.paid_at is not None for invoice in updated_invoices)
        untouched_invoice = db.session.get(Invoice, untouched_invoice_id)
        assert untouched_invoice is not None
        assert untouched_invoice.is_paid is False
        assert untouched_invoice.paid_at is None

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
        assert mark_unpaid_payload["status"] == "unpaid"
        assert len(mark_unpaid_payload["updated"]) == 2
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
        assert all(invoice.is_paid is False for invoice in unpaid_invoices)
        assert all(invoice.paid_at is None for invoice in unpaid_invoices)


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
        assert payload["message"] == "Select a valid payment status."


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
