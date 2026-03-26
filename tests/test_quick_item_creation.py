from werkzeug.security import generate_password_hash

from app import db
from app.models import GLCode, Item, ItemUnit, User, Vendor
from tests.utils import login


def test_purchase_order_page_has_quick_add(client, app):
    with app.app_context():
        user = User(
            email="poquick@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Quick", last_name="Vendor")
        db.session.add_all([user, vendor])
        db.session.commit()
    with client:
        login(client, "poquick@example.com", "pass")
        resp = client.get("/purchase_orders/create")
        assert resp.status_code == 200
        assert b'id="quick-add-item"' in resp.data


def test_quick_add_item_endpoint(client, app):
    with app.app_context():
        user = User(
            email="apiquick@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        gl = GLCode.query.filter_by(code="5000").first()
        gl_id = gl.id
        db.session.commit()
    with client:
        login(client, "apiquick@example.com", "pass")
        resp = client.post(
            "/items/quick_add",
            json={
                "name": "FastItem",
                "purchase_gl_code": gl_id,
                "base_unit": "each",
                "units": [
                    {
                        "name": "each",
                        "factor": 1,
                        "transfer_default": True,
                    },
                    {
                        "name": "case",
                        "factor": 12,
                        "receiving_default": True,
                    },
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["name"] == "FastItem"
        item_id = data["id"]
    with app.app_context():
        item = db.session.get(Item, item_id)
        assert item is not None
        assert item.base_unit == "each"
        assert item.purchase_gl_code_id == gl_id
        units = ItemUnit.query.filter_by(item_id=item_id).all()
        assert len(units) == 2
        each_unit = next(u for u in units if u.name == "each")
        case_unit = next(u for u in units if u.name == "case")
        assert case_unit.receiving_default is True
        assert case_unit.factor == 12
        assert each_unit.transfer_default is True
        assert each_unit.factor == 1
