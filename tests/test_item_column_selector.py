import os

from app import db
from app.models import Item
from tests.utils import login


def test_item_column_selector_checkboxes_render(client, app):
    with app.app_context():
        if Item.query.count() == 0:
            item = Item(name="Column Test Item", base_unit="each", cost=1.23)
            db.session.add(item)
            db.session.commit()

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    client.environ_base["wsgi.url_scheme"] = "https"
    with client:
        login_response = login(client, admin_email, admin_pass)
        assert login_response.status_code == 200
        assert login_response.request.path != "/auth/login"

        response = client.get("/items", follow_redirects=True)
        assert response.request.path == "/items"
        page = response.get_data(as_text=True)
        assert 'id="toggle-column-name"' in page
        assert 'data-column-target="col-name"' in page
        assert 'id="toggle-column-cost"' in page
        assert 'data-column-target="col-cost"' in page
