import pytest

from app import db, create_admin_user
from werkzeug.security import generate_password_hash
from app.models import ActivityLog, GLCode, Item, User
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
