import os
from io import BytesIO

from app import db
from app.models import GLCode
from tests.utils import login


def test_admin_can_import_gl_codes(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    data = {
        "gl_codes-file": (
            BytesIO(b"code,description\n7000,Test Code\n"),
            "gl_codes.csv",
        )
    }
    with client:
        login(client, admin_email, admin_pass)
        resp = client.post(
            "/controlpanel/import/gl_codes",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Imported 1 gl codes." in resp.data
    with app.app_context():
        assert GLCode.query.filter_by(code="7000").first() is not None


def test_admin_can_download_example_import_file(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        resp = client.get("/controlpanel/import/locations/example")
        assert resp.status_code == 200
        with app.app_context():
            path = os.path.join(
                app.config["IMPORT_FILES_FOLDER"], "example_locations.csv"
            )
            with open(path, "rb") as fh:
                expected = fh.read()
        assert resp.data == expected
