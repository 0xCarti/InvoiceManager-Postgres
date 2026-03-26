from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, ItemUnit, User
from tests.utils import login


def create_user(app, email="multi@example.com"):
    with app.app_context():
        user = User(
            email=email, password=generate_password_hash("pass"), active=True
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_add_item_multiple_units(client, app):
    create_user(app, "multiuser@example.com")
    with client:
        login(client, "multiuser@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "Combo",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
                "units-1-name": "case",
                "units-1-factor": 12,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        item = Item.query.filter_by(name="Combo").first()
        assert item is not None
        assert len(item.units) == 2
        assert sum(1 for u in item.units if u.receiving_default) == 1
        assert sum(1 for u in item.units if u.transfer_default) == 1


def test_allow_duplicate_unit_names(client, app):
    create_user(app, "dupunit@example.com")
    with client:
        login(client, "dupunit@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "DuplicateUnits",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "Bottle",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
                "units-1-name": "Bottle",
                "units-1-factor": 12,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        item = Item.query.filter_by(name="DuplicateUnits").first()
        assert item is not None
        assert len(item.units) == 2
        assert [u.name for u in sorted(item.units, key=lambda unit: unit.id)].count(
            "Bottle"
        ) == 2


def test_edit_item_shows_units_once(client, app):
    create_user(app, "editunits@example.com")
    with client:
        login(client, "editunits@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "Pack",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
                "units-1-name": "case",
                "units-1-factor": 12,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        with app.app_context():
            item = Item.query.filter_by(name="Pack").first()
            item_id = item.id
        resp = client.get(f"/items/edit/{item_id}")
        assert resp.status_code == 200
        page = resp.data.decode()
        assert page.count('name="units-0-name"') == 1
        assert page.count('name="units-1-name"') == 1


def test_reject_multiple_defaults(client, app):
    create_user(app, "dupdefault@example.com")
    with client:
        login(client, "dupdefault@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "BadItem",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-1-name": "box",
                "units-1-factor": 6,
                "units-1-receiving_default": "y",
            },
            follow_redirects=True,
        )
        assert b"Only one unit can be set as receiving" in resp.data
    with app.app_context():
        assert Item.query.filter_by(name="BadItem").first() is None
