import re
from datetime import date

import pytest

from app import db, create_admin_user
from werkzeug.security import generate_password_hash
from app.models import (
    ActivityLog,
    Event,
    EventLocation,
    GLCode,
    Item,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    User,
    Vendor,
)
from app.utils.activity import flush_activity_logs


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
        admin_id = admin.id
    with client.session_transaction() as session:
        session['_user_id'] = str(admin_id)
        session['_fresh'] = True


@pytest.fixture
def purchase_gl_code(app):
    with app.app_context():
        code = GLCode.query.filter(GLCode.code.like('5%')).first()
        if code is None:
            code = GLCode(code='5001')
            db.session.add(code)
            db.session.commit()
        return code


def test_bulk_update_items_success(client, app, purchase_gl_code):
    with app.app_context():
        item1 = Item(name='Item One', base_unit='each', archived=False)
        item2 = Item(name='Item Two', base_unit='each', archived=False)
        db.session.add_all([item1, item2])
        db.session.commit()
        item1_id, item2_id = item1.id, item2.id
        ids = f"{item1_id},{item2_id}"

    login_admin(client, app)
    response = client.post(
        '/items/bulk-update',
        data={
            'selected_ids': ids,
            'apply_purchase_gl_code_id': 'y',
            'purchase_gl_code_id': str(purchase_gl_code.id),
            'apply_archived': 'y',
            'archived': 'y',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert isinstance(payload.get('rows'), list)

    with app.app_context():
        item1 = db.session.get(Item, item1_id)
        item2 = db.session.get(Item, item2_id)
        assert item1.purchase_gl_code_id == purchase_gl_code.id
        assert item2.purchase_gl_code_id == purchase_gl_code.id
        assert item1.archived is True
        assert item2.archived is True
        flush_activity_logs()
        assert ActivityLog.query.filter(ActivityLog.activity.ilike('%Bulk updated items%')).count() == 1


def test_bulk_update_items_constraint_failure(client, app):
    with app.app_context():
        item1 = Item(name='Duplicate', base_unit='each', archived=True)
        item2 = Item(name='Duplicate', base_unit='each', archived=True)
        db.session.add_all([item1, item2])
        db.session.commit()
        item1_id, item2_id = item1.id, item2.id
        ids = f"{item1_id},{item2_id}"

    login_admin(client, app)
    response = client.post(
        '/items/bulk-update',
        data={
            'selected_ids': ids,
            'apply_archived': 'y',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is False
    assert 'form_html' in payload
    assert 'Cannot activate multiple items' in payload['form_html']

    with app.app_context():
        item1 = db.session.get(Item, item1_id)
        item2 = db.session.get(Item, item2_id)
        assert item1.archived is True
        assert item2.archived is True


def test_bulk_delete_items_archives_selected_rows(client, app):
    with app.app_context():
        item1 = Item(name="Bulk Delete One", base_unit="each", archived=False)
        item2 = Item(name="Bulk Delete Two", base_unit="each", archived=False)
        item3 = Item(name="Bulk Delete Three", base_unit="each", archived=True)
        db.session.add_all([item1, item2, item3])
        db.session.commit()
        item1_id, item2_id, item3_id = item1.id, item2.id, item3.id

    login_admin(client, app)

    list_response = client.get("/items", follow_redirects=True)
    assert list_response.status_code == 200

    html = list_response.get_data(as_text=True)
    csrf_match = re.search(
        r'<form id="bulk-delete-form" action="[^"]+" method="post">\s*<input id="csrf_token" name="csrf_token" type="hidden" value="([^"]+)"',
        html,
    )
    assert csrf_match is not None
    csrf_token = csrf_match.group(1)

    response = client.post(
        "/items/bulk_delete",
        data={
            "csrf_token": csrf_token,
            "item_ids": [str(item1_id), str(item2_id), str(item2_id), "bad-value"],
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Selected items have been archived and removed from current operational links." in response.data

    with app.app_context():
        item1 = db.session.get(Item, item1_id)
        item2 = db.session.get(Item, item2_id)
        item3 = db.session.get(Item, item3_id)
        assert item1.archived is True
        assert item2.archived is True
        assert item3.archived is True
        flush_activity_logs()
        assert (
            ActivityLog.query.filter(
                ActivityLog.activity.ilike("%Bulk archived items%")
            ).count()
            == 1
        )


def test_duplicate_items_page_groups_similar_names_with_last_received(client, app):
    with app.app_context():
        receiver = User(
            email="duplicate-receiver@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Duplicate", last_name="Vendor")
        location = Location(name="Duplicate Receiving Location")
        item1 = Item(name="Can - Molson Ultra", base_unit="each", archived=False)
        item2 = Item(name="Can - Molson Ultra 15", base_unit="each", archived=False)
        unrelated = Item(name="Paper Napkin", base_unit="each", archived=False)
        db.session.add_all([receiver, vendor, location, item1, item2, unrelated])
        db.session.commit()

        purchase_order = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=receiver.id,
            vendor_name="Duplicate Vendor",
            order_date=date(2024, 2, 1),
            expected_date=date(2024, 2, 2),
            received=True,
            status=PurchaseOrder.STATUS_RECEIVED,
        )
        db.session.add(purchase_order)
        db.session.commit()

        invoice = PurchaseInvoice(
            purchase_order_id=purchase_order.id,
            user_id=receiver.id,
            location_id=location.id,
            vendor_name="Duplicate Vendor",
            location_name=location.name,
            received_date=date(2024, 2, 3),
            invoice_number="DUP-1",
        )
        db.session.add(invoice)
        db.session.commit()

        db.session.add(
            PurchaseInvoiceItem(
                invoice_id=invoice.id,
                item_id=item2.id,
                item_name=item2.name,
                quantity=1,
                cost=2.5,
            )
        )
        db.session.commit()

    login_admin(client, app)

    list_response = client.get("/items", follow_redirects=True)
    assert list_response.status_code == 200
    assert b"Find Duplicates" in list_response.data

    response = client.get("/items/duplicates")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Potential Duplicate Items" in html
    assert "Can - Molson Ultra" in html
    assert "Can - Molson Ultra 15" in html
    assert "2024-02-03" in html
    assert "Paper Napkin" not in html


def test_duplicate_items_bulk_delete_returns_to_duplicate_report(client, app):
    with app.app_context():
        item1 = Item(name="Bottle - Duplicate Cola", base_unit="each", archived=False)
        item2 = Item(name="Bottle - Duplicate Cola 24", base_unit="each", archived=False)
        db.session.add_all([item1, item2])
        db.session.commit()
        item2_id = item2.id

    login_admin(client, app)

    report_response = client.get("/items/duplicates")
    html = report_response.get_data(as_text=True)
    csrf_match = re.search(
        r'<form id="duplicate-bulk-delete-form" action="[^"]+" method="post">\s*<input id="csrf_token" name="csrf_token" type="hidden" value="([^"]+)"',
        html,
    )
    assert csrf_match is not None
    csrf_token = csrf_match.group(1)

    response = client.post(
        "/items/bulk_delete",
        data={
            "csrf_token": csrf_token,
            "next": "/items/duplicates",
            "item_ids": [str(item2_id)],
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/items/duplicates")

    with app.app_context():
        assert db.session.get(Item, item2_id).archived is True


def test_delete_item_unlinks_current_recipe_and_location_records(client, app):
    with app.app_context():
        item = Item(name="Archive Target", base_unit="each", cost=2.5, archived=False)
        product = Product(
            name="Archive Product",
            price=8.0,
            cost=5.0,
            auto_update_recipe_cost=True,
            recipe_yield_quantity=1.0,
        )
        location = Location(name="Archive Location")
        db.session.add_all([item, product, location])
        db.session.commit()

        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                quantity=2.0,
                countable=True,
            )
        )
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                countable=True,
                expected_count=4.0,
            )
        )
        db.session.commit()
        item_id = item.id
        product_id = product.id
        location_id = location.id

    login_admin(client, app)

    list_response = client.get("/items", follow_redirects=True)
    html = list_response.get_data(as_text=True)
    csrf_match = re.search(
        r'<form id="bulk-delete-form" action="[^"]+" method="post">\s*<input id="csrf_token" name="csrf_token" type="hidden" value="([^"]+)"',
        html,
    )
    assert csrf_match is not None
    csrf_token = csrf_match.group(1)

    response = client.post(
        f"/items/delete/{item_id}",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Item archived and removed from current recipes and location sheets." in response.data

    with app.app_context():
        item = db.session.get(Item, item_id)
        product = db.session.get(Product, product_id)
        assert item.archived is True
        assert ProductRecipeItem.query.filter_by(product_id=product_id).count() == 0
        assert LocationStandItem.query.filter_by(location_id=location_id, item_id=item_id).count() == 0
        assert product.cost == pytest.approx(0.0)


def test_delete_item_blocked_while_open_event_uses_item(client, app):
    with app.app_context():
        item = Item(name="Blocked Archive Item", base_unit="each", archived=False)
        product = Product(name="Blocked Product", price=5.0, cost=1.0)
        location = Location(name="Blocked Location")
        event = Event(
            name="Open Event",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 2),
            closed=False,
        )
        db.session.add_all([item, product, location, event])
        db.session.commit()
        location.products.append(product)
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                quantity=1.0,
                countable=True,
            )
        )
        db.session.add(
            EventLocation(
                event_id=event.id,
                location_id=location.id,
                confirmed=False,
            )
        )
        db.session.commit()
        item_id = item.id

    login_admin(client, app)

    list_response = client.get("/items", follow_redirects=True)
    html = list_response.get_data(as_text=True)
    csrf_match = re.search(
        r'<form id="bulk-delete-form" action="[^"]+" method="post">\s*<input id="csrf_token" name="csrf_token" type="hidden" value="([^"]+)"',
        html,
    )
    assert csrf_match is not None
    csrf_token = csrf_match.group(1)

    response = client.post(
        f"/items/delete/{item_id}",
        data={"csrf_token": csrf_token},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Item cannot be archived while it is used by open events" in response.data

    with app.app_context():
        assert db.session.get(Item, item_id).archived is False
