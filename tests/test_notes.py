from werkzeug.security import generate_password_hash

from app import db
from app.models import ActivityLog, Location, Note, User
from app.utils.activity import flush_activity_logs
from tests.utils import extract_csrf_token, login


def _create_location_with_user(app):
    with app.app_context():
        admin = User.query.filter_by(email="admin@example.com").first()
        if admin is None:
            admin = User(
                email="admin@example.com",
                password=generate_password_hash("adminpass"),
                active=True,
                is_admin=True,
            )
            db.session.add(admin)
        location = Location(name="Main Warehouse")
        other_user = User(
            email="user@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([location, other_user])
        db.session.commit()
        return location.id


def test_admin_can_manage_notes(client, app):
    location_id = _create_location_with_user(app)

    with client:
        login(client, "admin@example.com", "adminpass")
        notes_page = client.get(f"/notes/location/{location_id}")
        notes_token = extract_csrf_token(notes_page)
        response = client.post(
            f"/notes/location/{location_id}",
            data={
                "csrf_token": notes_token,
                "content": "First note",
                "pinned": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

        with app.app_context():
            note = Note.query.filter_by(
                entity_type="location", entity_id=str(location_id)
            ).one()
            note_id = note.id
            assert note.pinned is True
            assert note.user.email == "admin@example.com"

        update_response = client.post(
            f"/notes/location/{location_id}/edit/{note_id}",
            data={
                "csrf_token": extract_csrf_token(
                    client.get(
                        f"/notes/location/{location_id}/edit/{note_id}"
                    )
                ),
                "content": "Updated note",
            },
            follow_redirects=True,
        )
        assert update_response.status_code == 200

        with app.app_context():
            refreshed = db.session.get(Note, note_id)
            assert refreshed.content == "Updated note"
            assert refreshed.pinned is False

        login(client, "user@example.com", "pass")
        delete_page = client.get(f"/notes/location/{location_id}")
        delete_token = extract_csrf_token(delete_page)

        delete_resp = client.post(
            f"/notes/location/{location_id}/delete/{note_id}",
            data={"csrf_token": delete_token},
            follow_redirects=False,
        )
        assert delete_resp.status_code == 403

        login(client, "admin@example.com", "adminpass")
        delete_page = client.get(f"/notes/location/{location_id}")
        delete_token = extract_csrf_token(delete_page)

        delete_resp = client.post(
            f"/notes/location/{location_id}/delete/{note_id}",
            data={"csrf_token": delete_token},
            follow_redirects=True,
        )
        assert delete_resp.status_code == 200
        assert "Note deleted" in delete_resp.get_data(as_text=True)

    with app.app_context():
        flush_activity_logs()
        assert Note.query.count() == 0
        activities = [entry.activity for entry in ActivityLog.query.all()]
        assert any("Added note to location" in activity for activity in activities)
        assert any("Updated note on location" in activity for activity in activities)
        assert any("Deleted note from location" in activity for activity in activities)


def test_non_admin_cannot_pin_notes(client, app):
    with app.app_context():
        location = Location(name="Secondary")
        user = User(
            email="noteuser@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add_all([location, user])
        db.session.commit()
        location_id = location.id

    with client:
        login(client, "noteuser@example.com", "pass")
        notes_page = client.get(f"/notes/location/{location_id}")
        notes_token = extract_csrf_token(notes_page)
        add_resp = client.post(
            f"/notes/location/{location_id}",
            data={
                "csrf_token": notes_token,
                "content": "Hello",
                "pinned": "y",
            },
            follow_redirects=True,
        )
        assert add_resp.status_code == 200

        with app.app_context():
            note = Note.query.filter_by(
                entity_type="location", entity_id=str(location_id)
            ).one()
            note_id = note.id
            assert note.pinned is False

        toggle = client.post(
            f"/notes/location/{location_id}/toggle-pin/{note_id}",
            data={"csrf_token": notes_token},
            follow_redirects=False,
        )
        assert toggle.status_code == 403

        edit_page = client.get(
            f"/notes/location/{location_id}/edit/{note_id}"
        )
        edit_token = extract_csrf_token(edit_page)
        update_resp = client.post(
            f"/notes/location/{location_id}/edit/{note_id}",
            data={
                "csrf_token": edit_token,
                "content": "Updated",
                "pinned": "y",
            },
            follow_redirects=True,
        )
        assert update_resp.status_code == 200

        with app.app_context():
            refreshed = db.session.get(Note, note_id)
            assert refreshed.content == "Updated"
            assert refreshed.pinned is False
