import pytest

from app import db, create_admin_user
from werkzeug.security import generate_password_hash
from app.models import ActivityLog, Location, Menu, User
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


def test_bulk_update_locations_success(client, app):
    with app.app_context():
        menu = Menu(name='Test Menu')
        location1 = Location(name='North', archived=False)
        location2 = Location(name='South', archived=False)
        db.session.add_all([menu, location1, location2])
        db.session.commit()
        menu_id = menu.id
        location1_id, location2_id = location1.id, location2.id
        ids = f"{location1_id},{location2_id}"

    login_admin(client, app)
    response = client.post(
        '/locations/bulk-update',
        data={
            'selected_ids': ids,
            'apply_menu_id': 'y',
            'menu_id': str(menu_id),
            'apply_is_spoilage': 'y',
            'is_spoilage': 'y',
            'apply_archived': 'y',
            'archived': 'y',
        },
        headers={'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True

    with app.app_context():
        location1 = db.session.get(Location, location1_id)
        location2 = db.session.get(Location, location2_id)
        assert location1.current_menu_id == menu_id
        assert location2.current_menu_id == menu_id
        assert location1.is_spoilage is True
        assert location2.is_spoilage is True
        assert location1.archived is True
        assert location2.archived is True
        flush_activity_logs()
        assert ActivityLog.query.filter(ActivityLog.activity.ilike('%Bulk updated locations%')).count() == 1


def test_bulk_update_locations_name_conflict(client, app):
    with app.app_context():
        existing = Location(name='Existing', archived=False)
        target = Location(name='Target', archived=False)
        db.session.add_all([existing, target])
        db.session.commit()
        target_id = target.id

    login_admin(client, app)
    response = client.post(
        '/locations/bulk-update',
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
        target = db.session.get(Location, target_id)
        assert target.name == 'Target'
