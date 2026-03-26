import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    GLCode,
    Item,
    ItemUnit,
    Product,
    ProductRecipeItem,
    User,
    Customer,
    Invoice,
    InvoiceProduct,
)
from tests.utils import login


def setup_data(app):
    with app.app_context():
        user = User(
            email="prodextra@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        item = Item(name="Sugar", base_unit="gram", cost=1.0)
        db.session.add_all([user, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="gram",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.commit()
        return user.email, item.id, unit.id


def test_additional_product_routes(client, app):
    email, item_id, unit_id = setup_data(app)
    alt_unit_id = None
    with client:
        login(client, email, "pass")
        # View and create product form (GET)
        assert client.get("/products").status_code == 200
        assert client.get("/products/create").status_code == 200
        with app.app_context():
            gl_id = GLCode.query.filter_by(code="4000").first().id
        resp = client.post(
            "/products/create",
            data={
                "name": "Candy",
                "price": 2,
                "cost": 1,
                "gl_code_id": gl_id,
                "recipe_yield_quantity": 35,
                "recipe_yield_unit": "cups",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 1,
                "items-0-countable": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        prod = Product.query.filter_by(name="Candy").first()
        assert prod.gl_code == "4000"
        assert prod.recipe_yield_quantity == pytest.approx(35.0)
        assert prod.recipe_yield_unit == "cups"
        pid = prod.id
        # add second recipe item for append_entry path
        db.session.add(
            ProductRecipeItem(
                product_id=pid,
                item_id=item_id,
                unit_id=unit_id,
                quantity=2,
                countable=True,
            )
        )
        alt_unit = ItemUnit(
            item_id=item_id,
            name="Half Gram",
            factor=0.5,
            receiving_default=False,
            transfer_default=False,
        )
        db.session.add(alt_unit)
        db.session.commit()
        alt_unit_id = alt_unit.id
    with client:
        login(client, email, "pass")
        # Edit page GET
        assert client.get(f"/products/{pid}/edit").status_code == 200
        assert (
            client.post(
                f"/products/{pid}/edit", data={}, follow_redirects=True
            ).status_code
            == 200
        )
        # Trigger gl_code lookup in edit by posting without gl_code
        resp = client.post(
            f"/products/{pid}/edit",
            data={
                "name": "Candy",
                "price": 3,
                "cost": 1,
                "gl_code_id": gl_id,
                "recipe_yield_quantity": 35,
                "recipe_yield_unit": "cups",
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 1,
                "items-0-countable": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
        # Recipe page GET should append entry for second recipe item
        assert client.get(f"/products/{pid}/recipe").status_code == 200
        # Calculate cost
        resp = client.get(f"/products/{pid}/calculate_cost")
        data = resp.get_json()
        assert data["cost"] == pytest.approx(1 / 35)
        assert data["batch_cost"] == pytest.approx(1.0)
        assert data["yield_quantity"] == pytest.approx(35.0)
        assert client.get("/products/999/calculate_cost").status_code == 404
        # Calculate cost preview using posted form data
        resp = client.post(
            "/products/calculate_cost_preview",
            json={
                "items": [
                    {"item_id": item_id, "unit_id": alt_unit_id, "quantity": 2},
                    {"item_id": item_id, "quantity": 1},
                    {"item_id": 9999, "quantity": 3},
                    {"item_id": item_id, "quantity": "invalid"},
                ],
                "yield_quantity": 10,
                "yield_unit": "cups",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["batch_cost"] == pytest.approx(2.0)
        assert data["cost"] == pytest.approx(0.2)
        assert data["yield_quantity"] == pytest.approx(10.0)
        resp = client.post(
            "/products/calculate_cost_preview",
            json={"items": [], "yield_quantity": 0},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["cost"] == pytest.approx(0.0)
        assert data["yield_quantity"] == pytest.approx(1.0)
        # Search products
        resp = client.get("/search_products?query=cand")
        assert b"Candy" in resp.data
        # Delete product
        assert (
            client.post(
                f"/products/{pid}/delete", follow_redirects=True
            ).status_code
            == 200
        )
        # 404 paths
        assert client.get("/products/999/edit").status_code == 404
        assert client.get("/products/999/recipe").status_code == 404


def test_view_products_sales_gl_code_filter(client, app):
    email, item_id, unit_id = setup_data(app)
    with app.app_context():
        gl1 = GLCode.query.filter_by(code="4000").first()
        gl2 = GLCode.query.filter_by(code="5000").first()
        gl1_id, gl2_id = gl1.id, gl2.id
        products = [
            Product(name=f"P{i}", price=1, cost=1, sales_gl_code_id=gl1_id)
            for i in range(21)
        ]
        products.append(
            Product(name="Other", price=1, cost=1, sales_gl_code_id=gl2_id)
        )
        db.session.add_all(products)
        db.session.commit()
    with client:
        login(client, email, "pass")
        resp = client.get(f"/products?sales_gl_code_id={gl1_id}")
        assert resp.status_code == 200
        assert b"P0" in resp.data
        assert b"Other" not in resp.data
        assert f"sales_gl_code_id={gl1_id}".encode() in resp.data


def test_view_products_customer_filter(client, app):
    email, item_id, unit_id = setup_data(app)
    with app.app_context():
        user = User.query.filter_by(email=email).first()
        cust1 = Customer(first_name="Alice", last_name="Smith")
        cust2 = Customer(first_name="Bob", last_name="Jones")
        prod1 = Product(name="ProdX", price=1, cost=1)
        prod2 = Product(name="ProdY", price=1, cost=1)
        db.session.add_all([cust1, cust2, prod1, prod2])
        db.session.commit()
        cust1_id = cust1.id
        inv1 = Invoice(id="INV1", user_id=user.id, customer_id=cust1_id)
        inv2 = Invoice(id="INV2", user_id=user.id, customer_id=cust2.id)
        db.session.add_all([inv1, inv2])
        db.session.commit()
        ip1 = InvoiceProduct(
            invoice_id=inv1.id,
            quantity=1,
            product_id=prod1.id,
            product_name=prod1.name,
            unit_price=1,
            line_subtotal=1,
            line_gst=0,
            line_pst=0,
        )
        ip2 = InvoiceProduct(
            invoice_id=inv2.id,
            quantity=1,
            product_id=prod2.id,
            product_name=prod2.name,
            unit_price=1,
            line_subtotal=1,
            line_gst=0,
            line_pst=0,
        )
        db.session.add_all([ip1, ip2])
        db.session.commit()
    with client:
        login(client, email, "pass")
        resp = client.get(f"/products?customer_id={cust1_id}")
        assert resp.status_code == 200
        assert b"ProdX" in resp.data
        assert b"ProdY" not in resp.data


def test_bulk_set_cost_from_recipe(client, app):
    email, item_id, unit_id = setup_data(app)
    with app.app_context():
        product = Product(
            name="BulkProd",
            price=5,
            cost=0,
            recipe_yield_quantity=3,
            recipe_yield_unit="cups",
        )
        db.session.add(product)
        db.session.commit()
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item_id,
                unit_id=unit_id,
                quantity=3,
            )
        )
        db.session.commit()
        product_id = product.id

    with client:
        login(client, email, "pass")
        resp = client.post(
            "/products/bulk_set_cost_from_recipe",
            data={"product_ids": [str(product_id)]},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Updated recipe cost" in resp.data

    with app.app_context():
        updated = db.session.get(Product, product_id)
        assert updated.cost == pytest.approx(1.0)
