from io import BytesIO

from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, User
from tests.utils import login


def create_user(app, email="import@example.com"):
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            active=True,
            is_admin=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.email


def test_csv_import_creates_item_with_cost_and_base_unit(client, app):
    email = create_user(app, "ext@example.com")
    with client:
        login(client, email, "pass")
        data = {
            "file": (
                BytesIO(b"name,base_unit,cost\nWidget,each,0.50\n"),
                "items.csv",
            )
        }
        resp = client.post(
            "/import_items",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"Imported 1 items successfully." in resp.data

    with app.app_context():
        item = Item.query.filter_by(name="Widget").first()
        assert item is not None
        assert item.base_unit == "each"
        assert item.cost == 0.5


def test_reject_unsupported_extension(client, app):
    email = create_user(app, "unsupported@example.com")
    with client:
        login(client, email, "pass")
        data = {"file": (BytesIO(b"item1\nitem2"), "items.xlsx")}
        resp = client.post(
            "/import_items",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"Only .csv and .txt files are allowed." in resp.data


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
