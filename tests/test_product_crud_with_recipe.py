from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, ItemUnit, Product, ProductRecipeItem, User
from tests.utils import login


def setup_user_and_items(app):
    with app.app_context():
        user = User(
            email="produser@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        item1 = Item(name="Flour", base_unit="gram", quantity=100)
        item2 = Item(name="Sugar", base_unit="gram", quantity=50)
        db.session.add_all([item1, item2])
        db.session.commit()
        iu1 = ItemUnit(
            item_id=item1.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        iu2 = ItemUnit(
            item_id=item2.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add_all([iu1, iu2])
        db.session.add(user)
        db.session.commit()
        return user.email, item1.id, item2.id, iu1.id, iu2.id


def test_create_product_without_recipe_items(client, app):
    email, *_ = setup_user_and_items(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/products/create",
            data={
                "name": "Simple Item",
                "price": 7,
                "cost": 3,
                "gl_code": "4000",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        product = Product.query.filter_by(name="Simple Item").first()
        assert product is not None
        assert len(product.recipe_items) == 0


def test_create_product_with_recipe_items(client, app):
    email, item1_id, item2_id, unit1_id, unit2_id = setup_user_and_items(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/products/create",
            data={
                "name": "Cake",
                "price": 5,
                "cost": 2,
                "gl_code": "4000",
                "items-0-item": item1_id,
                "items-0-unit": unit1_id,
                "items-0-quantity": 2,
                "items-0-countable": "y",
                "items-1-item": item2_id,
                "items-1-unit": unit2_id,
                "items-1-quantity": 1,
                "items-1-countable": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        product = Product.query.filter_by(name="Cake").first()
        assert product is not None
        assert len(product.recipe_items) == 2
        ids = {ri.item_id for ri in product.recipe_items}
        assert ids == {item1_id, item2_id}


def test_edit_product_recipe_on_edit_page(client, app):
    email, item1_id, item2_id, unit1_id, unit2_id = setup_user_and_items(app)
    with app.app_context():
        product = Product(name="Bread", price=3.0, cost=1.0)
        db.session.add(product)
        db.session.commit()
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item1_id,
                unit_id=unit1_id,
                quantity=1,
                countable=True,
            )
        )
        db.session.commit()
        pid = product.id
    with client:
        login(client, email, "pass")
        resp = client.post(
            f"/products/{pid}/edit",
            data={
                "name": "Bread",
                "price": 3.5,
                "cost": 1.5,
                "gl_code": "4000",
                "items-0-item": item2_id,
                "items-0-unit": unit2_id,
                "items-0-quantity": 4,
                "items-0-countable": "",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        product = db.session.get(Product, pid)
        assert product.price == 3.5
        assert len(product.recipe_items) == 1
        ri = product.recipe_items[0]
        assert ri.item_id == item2_id
        assert ri.quantity == 4


def test_recipe_accepts_integer_quantity(client, app):
    email, item1_id, _, unit1_id, _ = setup_user_and_items(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/products/create",
            data={
                "name": "Pie",
                "price": 4,
                "cost": 2,
                "gl_code": "4000",
                "items-0-item": item1_id,
                "items-0-unit": unit1_id,
                "items-0-quantity": 0,
                "items-0-countable": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        prod = Product.query.filter_by(name="Pie").first()
        assert prod is not None
        assert prod.recipe_items[0].quantity == 0


def test_recipe_accepts_decimal_quantity(client, app):
    email, item1_id, _, unit1_id, _ = setup_user_and_items(app)
    with client:
        login(client, email, "pass")
        resp = client.post(
            "/products/create",
            data={
                "name": "Muffin",
                "price": 3,
                "cost": 1,
                "gl_code": "4000",
                "items-0-item": item1_id,
                "items-0-unit": unit1_id,
                "items-0-quantity": 0.5,
                "items-0-countable": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        prod = Product.query.filter_by(name="Muffin").first()
        assert prod is not None
        assert prod.recipe_items[0].quantity == 0.5
