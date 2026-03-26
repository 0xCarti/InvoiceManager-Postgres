from io import BytesIO

from werkzeug.security import generate_password_hash

from app import db
from app.models import User
from tests.utils import login


def create_user(app, email="import@example.com"):
    with app.app_context():
        user = User(
            email=email, password=generate_password_hash("pass"), active=True
        )
        db.session.add(user)
        db.session.commit()
        return user.email


def test_reject_unsupported_extension(client, app):
    email = create_user(app, "ext@example.com")
    with client:
        login(client, email, "pass")
        data = {"file": (BytesIO(b"item1\nitem2"), "items.csv")}
        resp = client.post(
            "/import_items",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"Only .txt files are allowed." in resp.data


def test_reject_large_file(client, app):
    email = create_user(app, "large@example.com")
    with client:
        login(client, email, "pass")
        big_content = b"a" * (1 * 1024 * 1024 + 1)
        data = {"file": (BytesIO(big_content), "items.txt")}
        resp = client.post(
            "/import_items",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"File is too large." in resp.data
