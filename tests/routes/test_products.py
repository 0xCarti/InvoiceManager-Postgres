import pytest

from app import db, create_admin_user
from werkzeug.security import generate_password_hash
from app.models import ActivityLog, GLCode, Product, User
from app.utils.activity import flush_activity_logs
from tests.utils import login


def login_admin(client, app):
    with app.app_context():
        admin = User.query.filter_by(email='admin@example.com').first()
        if admin is None:
            create_admin_user()
            admin = User.query.filter_by(email='admin@example.com').first()
        if admin is None:
            admin = User(
                email='admin@example.com',
                password=generate_password_hash('adminpass'),
                active=True,
                is_admin=True,
            )
            db.session.add(admin)
            db.session.commit()
        else:
            admin.active = True
            admin.is_admin = True
            admin.password = generate_password_hash('adminpass')
            db.session.commit()
    login(client, 'admin@example.com', 'adminpass')


@pytest.fixture
def product_gl_codes(app):
    with app.app_context():
        sales = GLCode.query.filter(GLCode.code.like('4%')).first()
        if sales is None:
            sales = GLCode(code='4001')
            db.session.add(sales)
            db.session.commit()
        inventory = GLCode.query.filter(GLCode.code.like('5%')).first()
        if inventory is None:
            inventory = GLCode(code='5001')
            db.session.add(inventory)
            db.session.commit()
        return sales, inventory


def test_bulk_update_products_success(client, app, product_gl_codes):
    sales_gl, inventory_gl = product_gl_codes
    with app.app_context():
        product1 = Product(name='Product One', price=10.0, cost=5.0)
        product2 = Product(name='Product Two', price=8.0, cost=4.0)
        db.session.add_all([product1, product2])
        db.session.commit()
        product1_id, product2_id = product1.id, product2.id
        ids = f"{product1_id},{product2_id}"

    login_admin(client, app)
    response = client.post(
        '/products/bulk-update',
        data={
            'selected_ids': ids,
            'apply_price': 'y',
            'price': '12.75',
            'apply_cost': 'y',
            'cost': '6.25',
            'apply_sales_gl_code_id': 'y',
            'sales_gl_code_id': str(sales_gl.id),
            'apply_gl_code_id': 'y',
            'gl_code_id': str(inventory_gl.id),
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True

    with app.app_context():
        product1 = db.session.get(Product, product1_id)
        product2 = db.session.get(Product, product2_id)
        assert product1.price == pytest.approx(12.75)
        assert product2.price == pytest.approx(12.75)
        assert product1.cost == pytest.approx(6.25)
        assert product2.cost == pytest.approx(6.25)
        assert product1.sales_gl_code_id == sales_gl.id
        assert product2.sales_gl_code_id == sales_gl.id
        assert product1.gl_code_id == inventory_gl.id
        assert product2.gl_code_id == inventory_gl.id
        assert product1.gl_code == inventory_gl.code
        assert product2.gl_code == inventory_gl.code
        flush_activity_logs()
        assert ActivityLog.query.filter(ActivityLog.activity.ilike('%Bulk updated products%')).count() == 1


def test_bulk_update_products_name_conflict(client, app):
    with app.app_context():
        existing = Product(name='Existing', price=1.0, cost=1.0)
        target = Product(name='Target', price=2.0, cost=2.0)
        db.session.add_all([existing, target])
        db.session.commit()
        target_id = target.id

    login_admin(client, app)
    response = client.post(
        '/products/bulk-update',
        data={
            'selected_ids': str(target_id),
            'apply_name': 'y',
            'name': 'Existing',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is False
    assert 'already exists' in payload['form_html']

    with app.app_context():
        target = db.session.get(Product, target_id)
        assert target.name == 'Target'


def test_product_create_and_list_surfaces_show_both_price_labels(client, app):
    with app.app_context():
        product = Product(
            name='Dual Price Product',
            price=12.34,
            invoice_sale_price=15.67,
            cost=5.0,
        )
        db.session.add(product)
        db.session.commit()

    login_admin(client, app)

    list_response = client.get('/products')
    assert list_response.status_code == 200
    page = list_response.get_data(as_text=True)
    assert 'Terminal/Event Sell Price' in page
    assert 'Sales Invoice Price (3rd-party customer)' in page
    assert '12.34' in page
    assert '15.67' in page

    create_response = client.get('/products/create')
    assert create_response.status_code == 200
    create_page = create_response.get_data(as_text=True)
    assert 'Terminal/Event Sell Price' in create_page
    assert 'Sales Invoice Price (3rd-party customer)' in create_page


def test_search_products_requires_login_and_ignores_blank_query(client, app):
    with app.app_context():
        user = User(
            email="search@example.com",
            password=generate_password_hash("searchpass"),
            active=True,
            is_admin=True,
        )
        db.session.add(
            Product(name="Searchable Product", price=12.0, invoice_sale_price=15.0)
        )
        db.session.add(user)
        db.session.commit()

    anonymous = client.get("/search_products?query=Searchable")
    assert anonymous.status_code == 302
    assert "/auth/login" in anonymous.headers["Location"]

    with client:
        login(client, "search@example.com", "searchpass")

        blank = client.get("/search_products?query=")
        assert blank.status_code == 200
        assert blank.get_json() == []

        filled = client.get("/search_products?query=Searchable")
        assert filled.status_code == 200
        payload = filled.get_json()
        assert len(payload) == 1
        assert payload[0]["name"] == "Searchable Product"
