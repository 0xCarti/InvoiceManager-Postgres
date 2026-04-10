import os
from datetime import date, datetime
from itertools import count
import re

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Communication,
    CommunicationRecipient,
    Customer,
    Invoice,
    InvoiceProduct,
    Location,
    Product,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    Transfer,
    User,
    Vendor,
)
from app.services.dashboard_metrics import weekly_transfer_purchase_activity
from tests.permission_helpers import grant_permissions
from tests.utils import login

_INVOICE_SEQUENCE = count(1)


def _create_dashboard_user(email: str, password: str = "pass") -> User:
    user = User(
        email=email,
        password=generate_password_hash(password),
        active=True,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _create_basic_sale(user: User, *, when: datetime) -> Invoice:
    customer = Customer(first_name="Casey", last_name="Customer")
    product = Product(name="Espresso", price=5.0, cost=0.0, quantity=0.0)
    invoice = Invoice(
        id=f"INV{next(_INVOICE_SEQUENCE):03d}",
        customer=customer,
        creator=user,
        date_created=when,
    )
    invoice.products.append(
        InvoiceProduct(
            quantity=2,
            product=product,
            product_name=product.name,
            unit_price=5.0,
            line_subtotal=10.0,
            line_gst=0.0,
            line_pst=0.0,
        )
    )

    db.session.add_all([customer, product, invoice])

    return invoice


def _create_purchase_invoice(
    user: User,
    location: Location,
    vendor: Vendor,
    *,
    received_date: date,
    invoice_number: str,
    quantity: float = 1.0,
    cost: float = 8.0,
) -> PurchaseInvoice:
    purchase_order = PurchaseOrder(
        vendor_id=vendor.id,
        user_id=user.id,
        vendor_name=f"{vendor.first_name} {vendor.last_name}",
        order_date=received_date,
        expected_date=received_date,
        delivery_charge=0.0,
        received=True,
    )
    db.session.add(purchase_order)
    db.session.flush()

    invoice = PurchaseInvoice(
        purchase_order_id=purchase_order.id,
        user_id=user.id,
        location_id=location.id,
        vendor_name=f"{vendor.first_name} {vendor.last_name}",
        location_name=location.name,
        received_date=received_date,
        invoice_number=invoice_number,
        gst=0.0,
        pst=0.0,
        delivery_charge=0.0,
    )
    db.session.add(invoice)
    db.session.flush()

    db.session.add(
        PurchaseInvoiceItem(
            invoice_id=invoice.id,
            position=0,
            item_id=None,
            item_name="Coffee Beans",
            unit_name="case",
            quantity=quantity,
            cost=cost,
        )
    )
    return invoice


def _expected_interval_start(value: date, interval: str) -> date:
    if interval == "month":
        return value.replace(day=1)
    if interval == "quarter":
        return value.replace(month=((value.month - 1) // 3) * 3 + 1, day=1)
    if interval == "half_year":
        return value.replace(month=1 if value.month <= 6 else 7, day=1)
    if interval == "year":
        return value.replace(month=1, day=1)
    raise ValueError(f"Unsupported interval for test: {interval}")


def test_weekly_activity_includes_sales_totals(app):
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").first()
        loc_a = Location(name="Front")
        loc_b = Location(name="Back")
        db.session.add_all([loc_a, loc_b])
        db.session.flush()

        db.session.add(
            Transfer(
                from_location=loc_a,
                to_location=loc_b,
                creator=user,
                date_created=datetime(2024, 1, 9, 12, 0, 0),
            )
        )
        db.session.add(_create_basic_sale(user, when=datetime(2024, 1, 8, 10, 0, 0)))
        db.session.commit()

        activity = weekly_transfer_purchase_activity(weeks=2, today=date(2024, 1, 10))

        target_week = next(
            bucket
            for bucket in activity["buckets"]
            if bucket["week_start"] == "2024-01-08"
        )
        assert target_week["sales"] == 1
        assert target_week["sales_total"] == 10.0


@pytest.mark.parametrize(
    ("interval", "today", "first_boundary", "second_boundary"),
    [
        (
            "month",
            date(2024, 2, 20),
            datetime(2024, 1, 31, 23, 59, 59),
            datetime(2024, 2, 1, 0, 0, 0),
        ),
        (
            "quarter",
            date(2024, 5, 15),
            datetime(2024, 3, 31, 23, 59, 59),
            datetime(2024, 4, 1, 0, 0, 0),
        ),
        (
            "half_year",
            date(2024, 8, 15),
            datetime(2024, 6, 30, 23, 59, 59),
            datetime(2024, 7, 1, 0, 0, 0),
        ),
        (
            "year",
            date(2025, 3, 10),
            datetime(2024, 12, 31, 23, 59, 59),
            datetime(2025, 1, 1, 0, 0, 0),
        ),
    ],
)
def test_interval_rollovers_bucket_boundary_events_once(
    app, interval, today, first_boundary, second_boundary
):
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").first()
        loc_a = Location(name=f"{interval}-from")
        loc_b = Location(name=f"{interval}-to")
        vendor = Vendor(first_name="Boundary", last_name="Vendor")
        db.session.add_all([loc_a, loc_b, vendor])
        db.session.flush()

        db.session.add_all(
            [
                Transfer(
                    from_location=loc_a,
                    to_location=loc_b,
                    creator=user,
                    date_created=first_boundary,
                ),
                Transfer(
                    from_location=loc_a,
                    to_location=loc_b,
                    creator=user,
                    date_created=second_boundary,
                ),
            ]
        )
        db.session.add_all(
            [
                _create_basic_sale(user, when=first_boundary),
                _create_basic_sale(user, when=second_boundary),
            ]
        )
        _create_purchase_invoice(
            user,
            loc_a,
            vendor,
            received_date=first_boundary.date(),
            invoice_number=f"{interval}-A",
        )
        _create_purchase_invoice(
            user,
            loc_a,
            vendor,
            received_date=second_boundary.date(),
            invoice_number=f"{interval}-B",
        )
        db.session.commit()

        activity = weekly_transfer_purchase_activity(
            interval=interval,
            periods=2,
            today=today,
        )

        assert activity["interval"] == interval
        assert len(activity["buckets"]) == 2

        first_bucket, second_bucket = activity["buckets"]

        assert first_bucket["week_start"] == _expected_interval_start(
            first_boundary.date(), interval
        ).isoformat()
        assert second_bucket["week_start"] == _expected_interval_start(
            second_boundary.date(), interval
        ).isoformat()
        assert first_bucket["transfers"] == 1
        assert first_bucket["purchases"] == 1
        assert first_bucket["purchase_total"] == 8.0
        assert first_bucket["sales"] == 1
        assert first_bucket["sales_total"] == 10.0

        assert second_bucket["transfers"] == 1
        assert second_bucket["purchases"] == 1
        assert second_bucket["purchase_total"] == 8.0
        assert second_bucket["sales"] == 1
        assert second_bucket["sales_total"] == 10.0

        assert sum(bucket["transfers"] for bucket in activity["buckets"]) == 2
        assert sum(bucket["purchases"] for bucket in activity["buckets"]) == 2
        assert sum(bucket["sales"] for bucket in activity["buckets"]) == 2
        assert sum(bucket["purchase_total"] for bucket in activity["buckets"]) == 16.0
        assert sum(bucket["sales_total"] for bucket in activity["buckets"]) == 20.0


def test_dashboard_renders_sales_series(client, app):
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").first()
        loc_a = Location(name="North")
        loc_b = Location(name="South")
        db.session.add_all([loc_a, loc_b])
        db.session.flush()

        db.session.add(
            Transfer(
                from_location=loc_a,
                to_location=loc_b,
                creator=user,
                date_created=datetime.utcnow(),
            )
        )
        _create_basic_sale(user, when=datetime.utcnow())
        db.session.commit()

    login(client, "admin@example.com", os.getenv("ADMIN_PASS", "adminpass"))
    response = client.get("/", follow_redirects=True)
    body = response.data.decode()

    assert '"sales_total":' in body
    assert "$10.00" in body


def test_super_admin_dashboard_shows_metabase_button_when_configured(client, app):
    app.config["METABASE_SITE_URL"] = "https://reports.example.com"

    login(client, "admin@example.com", os.getenv("ADMIN_PASS", "adminpass"))
    response = client.get("/", follow_redirects=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Open Metabase" in body
    assert "Metabase Unavailable" not in body


def test_dashboard_shows_bulletin_card(client, app):
    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").first()
        bulletin = Communication(
            kind=Communication.KIND_BULLETIN,
            sender=user,
            audience_type=Communication.AUDIENCE_USERS,
            subject="Dashboard bulletin",
            body="Check the bulletin card on the dashboard.",
            pinned=True,
            active=True,
        )
        bulletin.recipients = [CommunicationRecipient(user_id=user.id)]
        db.session.add(bulletin)
        db.session.commit()

    login(client, "admin@example.com", os.getenv("ADMIN_PASS", "adminpass"))
    response = client.get("/", follow_redirects=True)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Bulletins" in body
    assert "Dashboard bulletin" in body
    assert "Check the bulletin card on the dashboard." in body


def test_dashboard_hides_metabase_button_without_permission(client, app):
    app.config["METABASE_SITE_URL"] = "http://metabase.localhost:3000"

    with app.app_context():
        user = _create_dashboard_user("dashboard-basic@example.com")
        grant_permissions(
            user,
            "dashboard.view",
            group_name="Dashboard Only",
            description="Can view the dashboard without Metabase access.",
        )

    login(client, "dashboard-basic@example.com", "pass")

    dashboard_response = client.get("/", follow_redirects=True)
    redirect_response = client.get("/metabase", follow_redirects=False)

    assert dashboard_response.status_code == 200
    assert "Open Metabase" not in dashboard_response.get_data(as_text=True)
    assert "Metabase Unavailable" not in dashboard_response.get_data(as_text=True)
    assert redirect_response.status_code == 403


def test_dashboard_shows_metabase_button_and_redirects_for_permitted_user(
    client, app
):
    app.config["METABASE_SITE_URL"] = "http://metabase.localhost:3000"

    with app.app_context():
        user = _create_dashboard_user("dashboard-metabase@example.com")
        grant_permissions(
            user,
            "dashboard.view",
            "reports.metabase",
            group_name="Dashboard Metabase",
            description="Can view the dashboard and open Metabase.",
        )

    login(client, "dashboard-metabase@example.com", "pass")

    dashboard_response = client.get("/", follow_redirects=True)
    redirect_response = client.get("/metabase", follow_redirects=False)
    dashboard_body = dashboard_response.get_data(as_text=True)

    assert dashboard_response.status_code == 200
    assert "Open Metabase" in dashboard_body
    assert 'href="/metabase"' in dashboard_body
    assert redirect_response.status_code == 302
    assert redirect_response.headers["Location"] == "http://metabase.localhost:3000"


@pytest.mark.parametrize(
    ("activity_interval", "expected_bucket_start"),
    [
        ("month", "2024-02-01"),
        ("quarter", "2024-01-01"),
    ],
)
def test_dashboard_activity_interval_selected_and_serialized(
    client, app, monkeypatch, activity_interval, expected_bucket_start
):
    fixed_today = date(2024, 2, 20)
    monkeypatch.setattr(
        "app.services.dashboard_metrics.current_user_today",
        lambda _value=None: fixed_today,
    )

    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").first()
        loc_a = Location(name="Interval North")
        loc_b = Location(name="Interval South")
        db.session.add_all([loc_a, loc_b])
        db.session.flush()

        db.session.add(
            Transfer(
                from_location=loc_a,
                to_location=loc_b,
                creator=user,
                date_created=datetime(2024, 2, 18, 12, 0, 0),
            )
        )
        _create_basic_sale(user, when=datetime(2024, 2, 18, 14, 30, 0))
        db.session.commit()

    login(client, "admin@example.com", os.getenv("ADMIN_PASS", "adminpass"))
    response = client.get(f"/?activity_interval={activity_interval}", follow_redirects=True)
    body = response.data.decode()

    assert response.status_code == 200
    assert re.search(
        rf'<option[^>]*(?:value="{activity_interval}"[^>]*selected|selected[^>]*value="{activity_interval}")',
        body,
    )
    assert re.search(rf'"interval"\s*:\s*"{activity_interval}"', body)
    assert re.search(rf'"week_start"\s*:\s*"{expected_bucket_start}"', body)


def test_dashboard_activity_interval_invalid_defaults_to_weekly(
    client, app, monkeypatch
):
    fixed_today = date(2024, 2, 20)
    monkeypatch.setattr(
        "app.services.dashboard_metrics.current_user_today",
        lambda _value=None: fixed_today,
    )

    with app.app_context():
        user = User.query.filter_by(email="admin@example.com").first()
        loc_a = Location(name="Fallback North")
        loc_b = Location(name="Fallback South")
        db.session.add_all([loc_a, loc_b])
        db.session.flush()

        db.session.add(
            Transfer(
                from_location=loc_a,
                to_location=loc_b,
                creator=user,
                date_created=datetime(2024, 2, 20, 9, 0, 0),
            )
        )
        _create_basic_sale(user, when=datetime(2024, 2, 20, 10, 15, 0))
        db.session.commit()

    login(client, "admin@example.com", os.getenv("ADMIN_PASS", "adminpass"))
    response = client.get("/?activity_interval=totally-invalid", follow_redirects=True)
    body = response.data.decode()

    assert response.status_code == 200
    assert re.search(
        r'<option[^>]*(?:value="weekly"[^>]*selected|selected[^>]*value="weekly")',
        body,
    )
    assert re.search(r'"interval"\s*:\s*"week"', body)
    assert re.search(r'"week_start"\s*:\s*"2024-02-19"', body)
