from werkzeug.security import generate_password_hash

from app import db
from app.models import GLCode, Item, ItemUnit, Product, ProductRecipeItem, User
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="gl@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        purchase = GLCode(code="5100")
        sales = GLCode.query.filter_by(code="4000").first()
        if sales is None:
            sales = GLCode(code="4000")
        item = Item(name="Widget", base_unit="each")
        db.session.add_all([user, purchase, sales, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.commit()
        return user.email, purchase.id, sales.id, item.id


def test_create_item_with_purchase_gl_code(client, app):
    email, purchase_id, sales_id, item_id = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "GLItem",
                "base_unit": "each",
                "purchase_gl_code": purchase_id,
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        item = Item.query.filter_by(name="GLItem").first()
        assert item is not None
        assert item.purchase_gl_code_id == purchase_id


def test_create_product_with_sales_gl_code(client, app):
    email, purchase_id, sales_id, item_id = setup_data(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/products/create",
            data={
                "name": "GLProduct",
                "price": 2,
                "cost": 1,
                "sales_gl_code": sales_id,
                "items-0-item": item_id,
                "items-0-quantity": 1,
                "items-0-countable": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        product = Product.query.filter_by(name="GLProduct").first()
        assert product is not None
        assert product.sales_gl_code_id == sales_id
