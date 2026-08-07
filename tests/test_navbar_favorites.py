import os

from app.models import User
from tests.utils import extract_csrf_token, login


def test_navbar_renders_single_favorites_row_without_special_admin_block(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert '<ul class="navbar-nav flex-row me-auto">' in html
        assert '<ul class="navbar-nav flex-row ms-auto">' not in html


def test_navbar_renders_when_favorite_endpoint_is_missing(
    client, app, monkeypatch
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    monkeypatch.setattr(
        User,
        "get_favorites",
        lambda self: ["missing.endpoint", "transfer.view_transfers"],
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert "Transfers" in html
    assert "missing.endpoint" not in html


def test_sidebar_group_links_keep_favorite_toggle_controls(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert 'aria-controls="nav-group-sales"' in html
    assert "/favorite/invoice.view_invoices" in html
    assert 'aria-label="Toggle favorite for Invoices"' in html
    assert "&#9733;" in html or "&#9734;" in html


def test_profile_favorite_toggle_is_keyboard_accessible(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert "/favorite/auth.profile" in html
    assert 'aria-label="Toggle favorite for Profile"' in html


def test_sidebar_menu_search_uses_reports_hub_instead_of_report_rows(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert 'id="sidebarMenuSearch"' in html
    assert "Search menu..." in html
    assert "No matches found" in html
    assert 'data-nav-endpoint="report.index"' in html
    assert 'data-nav-endpoint="report.customer_invoice_report"' not in html

    reports_response = client.get("/reports")
    assert reports_response.status_code == 200
    reports_html = reports_response.data.decode()
    assert "Customer Invoice Report" in reports_html
    assert "Product Location Sales Report" in reports_html
    assert "Event Spoilage Report" in reports_html


def test_sidebar_menu_search_includes_admin_destinations_for_admins(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert "Administration" in html
    assert 'data-nav-endpoint="admin.index"' in html
    assert 'data-nav-endpoint="admin.users"' not in html
    assert "/favorite/admin.index" in html
    assert 'aria-label="Toggle favorite for Administration"' in html

    admin_response = client.get("/controlpanel")
    assert admin_response.status_code == 200
    admin_html = admin_response.data.decode()
    assert "Users" in admin_html
    assert "Data Imports" in admin_html
    assert "System Info" in admin_html


def test_sidebar_limits_super_admin_to_primary_module_destinations(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/")

    assert response.status_code == 200
    html = response.data.decode()
    assert html.count('data-nav-endpoint="') == 19
    assert "Equipment Intake" not in html
    assert "Equipment Maintenance" not in html
    assert 'data-nav-endpoint="communication.messages"' not in html


def test_favorite_toggle_requires_post_and_updates_state(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        get_response = client.get("/auth/favorite/transfer.view_transfers")
        assert get_response.status_code == 405

        home = client.get("/")
        token = extract_csrf_token(home)
        post_response = client.post(
            "/auth/favorite/transfer.view_transfers",
            data={"csrf_token": token, "next": "/"},
            follow_redirects=False,
        )
        assert post_response.status_code == 302

        with app.app_context():
            admin = User.query.filter_by(email=admin_email).one()
            assert "transfer.view_transfers" in admin.get_favorites()
