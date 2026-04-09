from __future__ import annotations

from datetime import date

from werkzeug.security import generate_password_hash

from app import db
from app.models import Event, Item, User
from tests.permission_helpers import grant_item_workflow_permissions
from tests.utils import extract_csrf_token, login


def setup_filter_data(app):
    with app.app_context():
        user = User(
            email="defaults@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        active_item = Item(name="ActiveItem", base_unit="each", archived=False)
        archived_item = Item(name="ArchivedItem", base_unit="each", archived=True)
        db.session.add_all([user, active_item, archived_item])
        db.session.commit()
        grant_item_workflow_permissions(user)
        return user.email


def setup_event_filter_data(app):
    with app.app_context():
        inventory_event = Event(
            name="Inventory Event",
            start_date=date(2025, 1, 1),
            end_date=date(2025, 1, 2),
            event_type="inventory",
        )
        other_event = Event(
            name="Other Event",
            start_date=date(2025, 2, 1),
            end_date=date(2025, 2, 2),
            event_type="other",
        )
        db.session.add_all([inventory_event, other_event])
        db.session.commit()


def test_saved_defaults_apply_after_relogin(client, app, save_filter_defaults):
    email = setup_filter_data(app)
    with client:
        login(client, email, "pass")
        save_filter_defaults("item.view_items", {"archived": ["archived"]})

        logout_page = client.get("/items", follow_redirects=True)
        logout_token = extract_csrf_token(logout_page, required=False)
        logout_response = client.post(
            "/auth/logout",
            data={"csrf_token": logout_token} if logout_token else {},
            follow_redirects=True,
        )
        assert logout_response.status_code == 200

        login(client, email, "pass")
        redirect_response = client.get("/items")
        assert redirect_response.status_code == 302
        assert "archived=archived" in redirect_response.headers["Location"]

        final_response = client.get(redirect_response.headers["Location"])
        assert final_response.status_code == 200
        assert b"ArchivedItem" in final_response.data
        assert b"ActiveItem" not in final_response.data


def test_reset_reverts_to_saved_defaults(client, app, save_filter_defaults):
    email = setup_filter_data(app)
    with client:
        login(client, email, "pass")
        save_filter_defaults("item.view_items", {"archived": ["archived"]})

        active_response = client.get("/items?archived=active")
        assert active_response.status_code == 200
        assert b"ActiveItem" in active_response.data
        assert b"ArchivedItem" not in active_response.data

        reset_response = client.get("/items?reset=1")
        assert reset_response.status_code == 302
        assert "archived=archived" in reset_response.headers["Location"]

        final_response = client.get(reset_response.headers["Location"])
        assert final_response.status_code == 200
        assert b"ArchivedItem" in final_response.data
        assert b"ActiveItem" not in final_response.data


def test_save_defaults_button_shown_on_filter_views(client):
    with client:
        login(client, "admin@example.com", "adminpass")

        filter_views = (
            "/items",
            "/products",
            "/customers",
            "/events",
            "/gl_codes",
            "/view_invoices",
            "/locations",
            "/purchase_orders",
            "/purchase_invoices",
            "/spoilage",
            "/controlpanel/activity",
        )
        for path in filter_views:
            response = client.get(path)
            assert response.status_code == 200, path
            assert b"Save as Default" in response.data, path


def test_saved_event_defaults_apply_on_events_page(
    client, app, save_filter_defaults
):
    setup_event_filter_data(app)
    with client:
        login(client, "admin@example.com", "adminpass")
        save_filter_defaults(
            "event.view_events",
            {"type": ["inventory"]},
            token_path="/events",
        )

        redirect_response = client.get("/events")
        assert redirect_response.status_code == 302
        assert "type=inventory" in redirect_response.headers["Location"]

        final_response = client.get(redirect_response.headers["Location"])
        assert final_response.status_code == 200
        assert b"Inventory Event" in final_response.data
        assert b"Other Event" not in final_response.data
