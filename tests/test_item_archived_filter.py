from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, User
from tests.permission_helpers import grant_permissions
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="archived@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        for i in range(21):
            db.session.add(Item(name=f"A{i}", base_unit="each"))
        for i in range(2):
            db.session.add(Item(name=f"X{i}", base_unit="each", archived=True))
        db.session.commit()
        user = User.query.filter_by(email="archived@example.com").one()
        grant_permissions(
            user,
            "items.view",
            group_name="Item Archived Filter Test Group",
            description="Test permission for archived item filter.",
        )
        return user.email


def test_view_items_archived_filter(client, app):
    email = setup_data(app)
    with client:
        login(client, email, "pass")

        resp = client.get("/items")
        assert resp.status_code == 200
        assert b'<td class="col-name">A0</td>' in resp.data
        assert b'<td class="col-name">X0</td>' not in resp.data
        assert b"archived=active" in resp.data

        resp = client.get("/items?archived=archived")
        assert resp.status_code == 200
        assert b'<td class="col-name">A0</td>' not in resp.data
        assert b'<td class="col-name">X0</td>' in resp.data

        persisted = client.get("/items")
        assert persisted.status_code == 302
        assert "archived=archived" in persisted.headers["Location"]
        persisted_page = client.get(persisted.headers["Location"])
        assert persisted_page.status_code == 200
        assert b'<td class="col-name">X0</td>' in persisted_page.data
        assert b'<td class="col-name">A0</td>' not in persisted_page.data

        resp = client.get("/items?archived=all&page=2")
        assert resp.status_code == 200
        assert b'<td class="col-name">A9</td>' in resp.data
        assert b'<td class="col-name">X0</td>' in resp.data

