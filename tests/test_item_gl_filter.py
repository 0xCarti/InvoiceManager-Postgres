from werkzeug.security import generate_password_hash

from app import db
from app.models import GLCode, Item, User
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User.query.filter_by(email="filter@example.com").first()
        if not user:
            user = User(
                email="filter@example.com",
                password=generate_password_hash("pass"),
                active=True,
            )
            db.session.add(user)

        gl_codes_by_code = {
            code: GLCode.query.filter_by(code=code).first()
            for code in ("1000", "2000", "5000", "6000")
        }
        if None in gl_codes_by_code.values():
            sales_gl_food = gl_codes_by_code.get("1000") or GLCode(
                code="1000", description="Food Sales"
            )
            sales_gl_drink = gl_codes_by_code.get("2000") or GLCode(
                code="2000", description="Drink Sales"
            )
            purchase_gl_food = gl_codes_by_code.get("5000") or GLCode(
                code="5000", description="Food Purchases"
            )
            purchase_gl_drink = gl_codes_by_code.get("6000") or GLCode(
                code="6000", description="Drink Purchases"
            )
            db.session.add_all(
                [
                    sales_gl_food,
                    sales_gl_drink,
                    purchase_gl_food,
                    purchase_gl_drink,
                ]
            )
            db.session.flush()
        db.session.commit()

        if not Item.query.filter_by(name="A0").first():
            sales_gl_food = GLCode.query.filter_by(code="1000").first()
            purchase_gl_food = GLCode.query.filter_by(code="5000").first()
            sales_gl_drink = GLCode.query.filter_by(code="2000").first()
            purchase_gl_drink = GLCode.query.filter_by(code="6000").first()
            for i in range(5):
                db.session.add(
                    Item(
                        name=f"A{i}",
                        base_unit="each",
                        gl_code_id=sales_gl_food.id,
                        purchase_gl_code_id=purchase_gl_food.id,
                    )
                )
            db.session.add(
                Item(
                    name="B0",
                    base_unit="each",
                    gl_code_id=sales_gl_drink.id,
                    purchase_gl_code_id=purchase_gl_drink.id,
                )
            )
            db.session.commit()

        sales_gl_food = GLCode.query.filter_by(code="1000").first()
        sales_gl_drink = GLCode.query.filter_by(code="2000").first()
        purchase_gl_food = GLCode.query.filter_by(code="5000").first()
        purchase_gl_drink = GLCode.query.filter_by(code="6000").first()
        return {
            "email": user.email,
            "sales_gl_ids": [sales_gl_food.id, sales_gl_drink.id],
            "sales_gl_codes": [sales_gl_food.code, sales_gl_drink.code],
            "purchase_gl_ids": [purchase_gl_food.id, purchase_gl_drink.id],
            "purchase_gl_codes": [
                purchase_gl_food.code,
                purchase_gl_drink.code,
            ],
        }


def test_view_items_filter_by_gl_code(client, app):
    data = setup_data(app)
    email = data["email"]
    gl1_id = data["sales_gl_ids"][0]
    gl_code = data["sales_gl_codes"][0]
    with client:
        login(client, email, "pass")
        resp = client.get(f"/items?gl_code_id={gl1_id}")
        assert resp.status_code == 200
        assert b"A0" in resp.data
        assert b"B0" not in resp.data
        assert b"Filtering by Inventory GL Code" in resp.data
        assert gl_code.encode() in resp.data


def test_view_items_filter_by_multiple_gl_codes(client, app):
    data = setup_data(app)
    email = data["email"]
    gl1_id, gl2_id = data["sales_gl_ids"]
    with client:
        login(client, email, "pass")
        resp = client.get(f"/items?gl_code_id={gl1_id}&gl_code_id={gl2_id}")
        assert resp.status_code == 200
        assert b"A0" in resp.data
        assert b"B0" in resp.data
        assert b"Filtering by Inventory GL Code" in resp.data


def test_view_items_filter_by_purchase_gl_code(client, app):
    data = setup_data(app)
    email = data["email"]
    purchase_gl_id = data["purchase_gl_ids"][0]
    purchase_gl_code = data["purchase_gl_codes"][0]
    with client:
        login(client, email, "pass")
        resp = client.get(f"/items?purchase_gl_code_id={purchase_gl_id}")
        assert resp.status_code == 200
        assert b"A0" in resp.data
        assert b"B0" not in resp.data
        assert b"Filtering by Purchase GL Code" in resp.data
        assert purchase_gl_code.encode() in resp.data
