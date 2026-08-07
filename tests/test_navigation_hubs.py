from werkzeug.security import generate_password_hash

from app import db
from app.models import User
from tests.permission_helpers import grant_permissions
from tests.utils import login


def _create_user_with_permissions(app, email, *permission_codes):
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.flush()
        grant_permissions(
            user,
            *permission_codes,
            group_name=f"Navigation permissions for {email}",
            description="Narrow permissions used to verify navigation hubs.",
        )
    return email


def test_report_only_user_reaches_filtered_reports_hub(client, app):
    email = _create_user_with_permissions(
        app,
        "report-nav@example.com",
        "reports.product_location_sales",
    )

    with client:
        login(client, email, "pass")
        profile_response = client.get("/auth/profile")
        reports_response = client.get("/reports")

    assert profile_response.status_code == 200
    profile_html = profile_response.data.decode()
    assert 'data-nav-endpoint="report.index"' in profile_html
    assert 'data-nav-endpoint="report.product_location_sales_report"' not in profile_html

    assert reports_response.status_code == 200
    reports_html = reports_response.data.decode()
    assert "Product Location Sales Report" in reports_html
    assert "Event Spoilage Report" not in reports_html
    assert "Customer Invoice Report" not in reports_html


def test_invoice_gl_report_permission_reaches_purchase_invoice_selection(
    client, app
):
    email = _create_user_with_permissions(
        app,
        "invoice-gl-nav@example.com",
        "reports.invoice_gl_codes",
        "purchase_invoices.view",
    )

    with client:
        login(client, email, "pass")
        profile_response = client.get("/auth/profile")
        reports_response = client.get("/reports")

    assert profile_response.status_code == 200
    assert 'data-nav-endpoint="report.index"' in profile_response.data.decode()
    assert reports_response.status_code == 200
    reports_html = reports_response.data.decode()
    assert "Purchase Invoice GL Report" in reports_html
    assert 'href="/purchase_invoices"' in reports_html


def test_settings_only_user_reaches_filtered_administration_hub(client, app):
    email = _create_user_with_permissions(
        app,
        "admin-nav@example.com",
        "settings.view",
    )

    with client:
        login(client, email, "pass")
        profile_response = client.get("/auth/profile")
        admin_response = client.get("/controlpanel")

    assert profile_response.status_code == 200
    assert 'data-nav-endpoint="admin.index"' in profile_response.data.decode()

    assert admin_response.status_code == 200
    admin_html = admin_response.data.decode()
    assert "Settings" in admin_html
    assert "Users" not in admin_html
    assert "Backups" not in admin_html


def test_singleton_modules_choose_a_permitted_local_destination(client, app):
    email = _create_user_with_permissions(
        app,
        "module-nav@example.com",
        "schedules.manage_self_availability",
        "signage.manage_media",
    )

    with client:
        login(client, email, "pass")
        response = client.get("/auth/profile")

    assert response.status_code == 200
    html = response.data.decode()
    assert 'data-nav-endpoint="schedule.availability"' in html
    assert 'data-nav-endpoint="signage.view_signage_media_assets"' in html
    assert 'data-nav-endpoint="schedule.team_schedule"' not in html
    assert 'data-nav-endpoint="signage.view_displays"' not in html
