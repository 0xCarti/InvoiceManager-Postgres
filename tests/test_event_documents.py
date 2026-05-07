from __future__ import annotations

from io import BytesIO
from pathlib import Path
from datetime import date

from werkzeug.security import generate_password_hash

from app import db
from app.models import Event, EventDocument, EventLocation, Location, User
from tests.permission_helpers import grant_event_permissions, grant_permissions
from tests.utils import login


def _seed_event_document_user(app, *, email: str, permission_codes: tuple[str, ...]):
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            active=True,
        )
        location = Location(name=f"Document Location {email}")
        event = Event(
            name=f"Document Event {email}",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 2),
            event_type="inventory",
        )
        db.session.add_all([user, location, event])
        db.session.flush()
        db.session.add(EventLocation(event_id=event.id, location_id=location.id))
        db.session.commit()

        if permission_codes == ("all",):
            grant_event_permissions(user)
        else:
            grant_permissions(
                user,
                *permission_codes,
                group_name=f"Event Document Permissions {email}",
                description="Test permissions for event documents.",
            )

        return {
            "email": user.email,
            "event_id": event.id,
        }


def test_event_document_upload_download_and_delete_flow(client, app):
    seeded = _seed_event_document_user(
        app,
        email="event-doc-manager@example.com",
        permission_codes=("all",),
    )

    upload_content = b"%PDF-1.4 event floor plan"

    with client:
        login(client, seeded["email"], "pass")
        page = client.get(f"/events/{seeded['event_id']}")
        assert page.status_code == 200
        assert b'data-event-document-form="1"' in page.data

        response = client.post(
            f"/events/{seeded['event_id']}/documents",
            data={
                "name": "Floor Plan",
                "file": (BytesIO(upload_content), "floor-layout.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Event document uploaded successfully." in response.data
        assert b"Floor Plan" in response.data
        assert b"Original file: floor-layout.pdf" in response.data

    with app.app_context():
        document = EventDocument.query.one()
        document_id = document.id
        storage_path = Path(document.storage_path)
        assert document.display_name == "Floor Plan"
        assert document.download_name == "Floor Plan.pdf"
        assert storage_path.exists()

    with client:
        login(client, seeded["email"], "pass")
        download_response = client.get(
            f"/events/{seeded['event_id']}/documents/{document_id}/download"
        )
        assert download_response.status_code == 200
        assert download_response.data == upload_content
        assert "Floor Plan.pdf" in download_response.headers["Content-Disposition"]

        delete_response = client.post(
            f"/events/{seeded['event_id']}/documents/{document_id}/delete",
            data={},
            follow_redirects=True,
        )
        assert delete_response.status_code == 200
        assert b"Event document deleted." in delete_response.data

    with app.app_context():
        assert EventDocument.query.count() == 0
        assert not storage_path.exists()


def test_event_document_viewers_can_download_but_not_manage(client, app):
    editor = _seed_event_document_user(
        app,
        email="event-doc-editor@example.com",
        permission_codes=("all",),
    )
    viewer = _seed_event_document_user(
        app,
        email="event-doc-viewer@example.com",
        permission_codes=("events.view",),
    )

    with client:
        login(client, editor["email"], "pass")
        upload_response = client.post(
            f"/events/{editor['event_id']}/documents",
            data={
                "use_current_filename": "y",
                "file": (BytesIO(b"menu data"), "menu plan.pdf"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert upload_response.status_code == 200

    with app.app_context():
        document = EventDocument.query.filter_by(event_id=editor["event_id"]).one()
        document_id = document.id

    with client:
        login(client, viewer["email"], "pass")
        detail_response = client.get(f"/events/{editor['event_id']}")
        assert detail_response.status_code == 200
        assert b'data-event-document-form="1"' not in detail_response.data
        assert b"Use current filename" not in detail_response.data
        assert b"menu plan.pdf" in detail_response.data

        forbidden_upload = client.post(
            f"/events/{editor['event_id']}/documents",
            data={
                "use_current_filename": "y",
                "file": (BytesIO(b"viewer upload"), "viewer.pdf"),
            },
            content_type="multipart/form-data",
        )
        assert forbidden_upload.status_code == 403

        forbidden_delete = client.post(
            f"/events/{editor['event_id']}/documents/{document_id}/delete",
            data={},
        )
        assert forbidden_delete.status_code == 403

        allowed_download = client.get(
            f"/events/{editor['event_id']}/documents/{document_id}/download"
        )
        assert allowed_download.status_code == 200
        assert allowed_download.data == b"menu data"


def test_event_delete_cleans_up_uploaded_document_storage(client, app):
    seeded = _seed_event_document_user(
        app,
        email="event-doc-delete@example.com",
        permission_codes=("all",),
    )

    with client:
        login(client, seeded["email"], "pass")
        upload_response = client.post(
            f"/events/{seeded['event_id']}/documents",
            data={
                "use_current_filename": "y",
                "file": (BytesIO(b"delete me"), "notes.txt"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert upload_response.status_code == 200

    with app.app_context():
        document = EventDocument.query.filter_by(event_id=seeded["event_id"]).one()
        storage_path = Path(document.storage_path)
        assert storage_path.exists()

    with client:
        login(client, seeded["email"], "pass")
        delete_event_response = client.post(
            f"/events/{seeded['event_id']}/delete",
            data={},
            follow_redirects=True,
        )
        assert delete_event_response.status_code == 200
        assert b"Event deleted" in delete_event_response.data

    with app.app_context():
        assert db.session.get(Event, seeded["event_id"]) is None
        assert EventDocument.query.count() == 0
        assert not storage_path.exists()
