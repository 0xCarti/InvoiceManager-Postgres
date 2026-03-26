from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, ItemUnit, User
from tests.utils import login


def create_user(app, email="reuse@example.com"):
    with app.app_context():
        user = User(
            email=email, password=generate_password_hash("pass"), active=True
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_item_name_reuse_after_delete(client, app):
    create_user(app, "reuse@example.com")
    with client:
        login(client, "reuse@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "Reusable",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        item = Item.query.filter_by(name="Reusable", archived=False).first()
        assert item is not None
        item_id = item.id

    with client:
        login(client, "reuse@example.com", "pass")
        resp = client.post(f"/items/delete/{item_id}", follow_redirects=True)
        assert resp.status_code == 200

    with client:
        login(client, "reuse@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "Reusable",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        items = Item.query.filter_by(name="Reusable").all()
        assert len(items) == 2
        active = [i for i in items if not i.archived]
        assert len(active) == 1
