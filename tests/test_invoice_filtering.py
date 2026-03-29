from datetime import datetime

from werkzeug.security import generate_password_hash

from app import db
from app.models import Customer, Invoice, User
from tests.utils import login


def setup_invoices(app):
    with app.app_context():
        user = User(
            email="inv@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        c1 = Customer(first_name="Alpha", last_name="One")
        c2 = Customer(first_name="Beta", last_name="Two")
        db.session.add_all([user, c1, c2])
        db.session.commit()

        i1 = Invoice(
            id="INV1",
            user_id=user.id,
            customer_id=c1.id,
            date_created=datetime(2023, 1, 1),
        )
        i2 = Invoice(
            id="INV2",
            user_id=user.id,
            customer_id=c2.id,
            date_created=datetime(2023, 2, 1),
        )
        i3 = Invoice(
            id="INV3",
            user_id=user.id,
            customer_id=c1.id,
            date_created=datetime(2023, 3, 1),
        )
        db.session.add_all([i1, i2, i3])
        db.session.commit()

        return user.email, c1.id, c2.id


def test_filter_by_invoice_id(client, app):
    user_email, c1_id, c2_id = setup_invoices(app)
    with client:
        login(client, user_email, "pass")
        response = client.get(
            "/view_invoices?invoice_id=INV2",
            follow_redirects=True,
        )
        assert b"INV2" in response.data
        assert b"INV1" not in response.data
        assert b"INV3" not in response.data


def test_filter_by_customer(client, app):
    user_email, c1_id, c2_id = setup_invoices(app)
    with client:
        login(client, user_email, "pass")
        response = client.get(
            f"/view_invoices?customer_id={c1_id}",
            follow_redirects=True,
        )
        assert b"INV1" in response.data
        assert b"INV3" in response.data
        assert b"INV2" not in response.data


def test_filter_by_date_range(client, app):
    user_email, c1_id, c2_id = setup_invoices(app)
    with client:
        login(client, user_email, "pass")
        response = client.get(
            "/view_invoices?start_date=2023-02-01&end_date=2023-03-01",
            follow_redirects=True,
        )
        assert b"INV1" not in response.data
        assert b"INV2" in response.data
        assert b"INV3" in response.data


def test_filter_by_payment_status(client, app):
    user_email, _, _ = setup_invoices(app)

    with app.app_context():
        inv1 = db.session.get(Invoice, "INV1")
        inv2 = db.session.get(Invoice, "INV2")
        inv3 = db.session.get(Invoice, "INV3")
        inv1.is_paid = True
        inv2.is_paid = False
        inv3.is_paid = True
        db.session.commit()

    with client:
        login(client, user_email, "pass")
        paid_response = client.get(
            "/view_invoices?payment_status=paid",
            follow_redirects=True,
        )
        assert b"INV1" in paid_response.data
        assert b"INV3" in paid_response.data
        assert b"INV2" not in paid_response.data

        unpaid_response = client.get(
            "/view_invoices?payment_status=unpaid",
            follow_redirects=True,
        )
        assert b"INV2" in unpaid_response.data
        assert b"INV1" not in unpaid_response.data
        assert b"INV3" not in unpaid_response.data


def test_view_invoices_rejects_invalid_filter_dates(client, app):
    user_email, _, _ = setup_invoices(app)

    with client:
        login(client, user_email, "pass")
        response = client.get(
            "/view_invoices?start_date=not-a-date",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Invalid start date." in response.data


def test_filter_invoices_api_rejects_invalid_filter_dates(client, app):
    user_email, _, _ = setup_invoices(app)

    with client:
        login(client, user_email, "pass")
        response = client.get("/api/filter_invoices?end_date=bad-date")

    assert response.status_code == 400
    assert response.get_json()["errors"]["end_date"] == ["Invalid end date."]


def test_filter_invoices_api_rejects_reversed_date_range(client, app):
    user_email, _, _ = setup_invoices(app)

    with client:
        login(client, user_email, "pass")
        response = client.get(
            "/api/filter_invoices?start_date=2023-03-01&end_date=2023-02-01"
        )

    assert response.status_code == 400
    assert response.get_json()["errors"]["end_date"] == [
        "Invalid date range: start cannot be after end."
    ]


def test_view_invoices_reports_dropdown_and_layout_markers(client, app):
    user_email, _, _ = setup_invoices(app)

    with client:
        login(client, user_email, "pass")
        response = client.get("/view_invoices", follow_redirects=True)

    html = response.get_data(as_text=True)
    assert response.status_code == 200

    # Consolidated reports dropdown remains present.
    assert "dropdown-toggle\" type=\"button\" data-bs-toggle=\"dropdown\"" in html
    assert ">\n                Reports\n            </button>" in html
    assert "dropdown-item" in html

    # Old standalone report-button cluster is absent (no direct report buttons).
    assert "btn btn-secondary mb-3\">Vendor Report</a>" not in html
    assert "btn btn-secondary mb-3\">Revenue Report</a>" not in html

    # Header create action and utility controls are in distinct blocks.
    assert "d-flex flex-column flex-md-row justify-content-between" in html
    assert "Create Invoice" in html
    assert "d-flex flex-wrap align-items-center gap-2 mb-3" in html
    assert 'id="invoice-search"' in html
    assert "data-bs-target=\"#filterModal\"" in html


def test_view_invoices_actions_column_uses_overflow_menu_with_delete(client, app):
    user_email, _, _ = setup_invoices(app)

    with client:
        login(client, user_email, "pass")
        response = client.get("/view_invoices", follow_redirects=True)

    html = response.get_data(as_text=True)
    assert response.status_code == 200

    assert 'class="col-invoice-actions" data-sortable="false">Actions</th>' in html
    assert 'class="btn btn-primary mr-2">View</a>' in html
    assert 'aria-label="Invoice actions"' in html
    assert 'class="js-confirm-delete-invoice"' in html
    assert 'method="post"' in html
    assert 'class="dropdown-item text-danger">Delete</button>' in html


def test_view_invoices_filter_form_submits_to_server_route(client, app):
    user_email, _, _ = setup_invoices(app)

    with client:
        login(client, user_email, "pass")
        response = client.get("/view_invoices", follow_redirects=True)

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert 'action="/view_invoices"' in html
    assert "/api/filter_invoices" not in html


def test_invoice_customer_filter_lists_all_customers_on_later_pages(client, app):
    with app.app_context():
        user = User(
            email="invoicefilterchoices@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

        first_customer_id = None
        last_customer_id = None
        for index in range(30):
            customer = Customer(first_name=f"Customer{index:02d}", last_name="Filter")
            db.session.add(customer)
            db.session.flush()
            if first_customer_id is None:
                first_customer_id = customer.id
            last_customer_id = customer.id
            db.session.add(
                Invoice(
                    id=f"INVF{index:02d}",
                    user_id=user.id,
                    customer_id=customer.id,
                    date_created=datetime(2023, 1, 1),
                )
            )
        db.session.commit()

    with client:
        login(client, "invoicefilterchoices@example.com", "pass")
        response = client.get("/view_invoices?page=2", follow_redirects=True)

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert f'value="{first_customer_id}"'.encode() in response.data
    assert f'value="{last_customer_id}"'.encode() in response.data
    assert "Page 2 of 2" in html
