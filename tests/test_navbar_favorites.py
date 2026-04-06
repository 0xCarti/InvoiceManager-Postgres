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


def test_sidebar_menu_search_includes_report_destinations(client, app):
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
    assert (
        'data-nav-endpoint="report.customer_invoice_report"' in html
    )


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
    assert "System/Admin" in html
    assert 'data-nav-endpoint="admin.users"' in html
    assert "/favorite/admin.users" in html
    assert 'aria-label="Toggle favorite for Control Panel"' in html


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
