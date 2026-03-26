from __future__ import annotations

from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, User
from tests.utils import login


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
        return user.email


def test_saved_defaults_apply_after_relogin(client, app, save_filter_defaults):
    email = setup_filter_data(app)
    with client:
        login(client, email, "pass")
        save_filter_defaults("item.view_items", {"archived": ["archived"]})

        logout_response = client.get("/auth/logout", follow_redirects=True)
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
