from uuid import uuid4

from werkzeug.security import generate_password_hash

from app import db
from app.models import Item, ItemBarcode, ItemUnit, User
from tests.utils import login


def _create_user(app, email):
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()


def test_add_and_edit_item_barcodes(client, app):
    email = f"barcode_{uuid4().hex[:8]}@example.com"
    _create_user(app, email)

    with client:
        login(client, email, "pass")
        response = client.post(
            "/items/add",
            data={
                "name": "Barcode Item",
                "base_unit": "each",
                "gl_code": "5000",
                "barcodes-0-code": "111111111111",
                "barcodes-1-code": "222222222222",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        item = Item.query.filter_by(name="Barcode Item").first()
        assert item is not None
        item_id = item.id
        assert item.upc == "111111111111"
        assert item.barcode_values == ["111111111111", "222222222222"]
        aliases = ItemBarcode.query.filter_by(item_id=item.id).all()
        assert [alias.code for alias in aliases] == ["222222222222"]

    with client:
        login(client, email, "pass")
        response = client.post(
            f"/items/edit/{item_id}",
            data={
                "name": "Barcode Item",
                "base_unit": "each",
                "gl_code": "5000",
                "barcodes-0-code": "333333333333",
                "barcodes-1-code": "111111111111",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200

    with app.app_context():
        updated = db.session.get(Item, item_id)
        assert updated is not None
        assert updated.upc == "333333333333"
        assert updated.barcode_values == ["333333333333", "111111111111"]


def test_barcodes_must_be_unique_across_items(client, app):
    email = f"barcode_conflict_{uuid4().hex[:8]}@example.com"
    _create_user(app, email)

    with app.app_context():
        item = Item(name="Existing Barcode Item", base_unit="each", upc="444444444444")
        db.session.add(item)
        db.session.commit()
        db.session.add(ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        ))
        db.session.add(ItemBarcode(item_id=item.id, code="555555555555"))
        db.session.commit()

    with client:
        login(client, email, "pass")
        response = client.post(
            "/items/add",
            data={
                "name": "Conflicting Barcode Item",
                "base_unit": "each",
                "gl_code": "5000",
                "barcodes-0-code": "555555555555",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b'already assigned to Existing Barcode Item' in response.data

    with app.app_context():
        assert Item.query.filter_by(name="Conflicting Barcode Item").first() is None


def test_item_search_matches_barcode_alias(client, app):
    email = f"barcode_search_{uuid4().hex[:8]}@example.com"
    _create_user(app, email)

    with app.app_context():
        item = Item(name="Searchable Barcode Item", base_unit="each")
        db.session.add(item)
        db.session.commit()
        db.session.add(ItemBarcode(item_id=item.id, code="666666666666"))
        db.session.add(
            ItemUnit(
                item_id=item.id,
                name="each",
                factor=1,
                receiving_default=True,
                transfer_default=True,
            )
        )
        db.session.commit()

    with client:
        login(client, email, "pass")
        response = client.get("/items/search?term=666666666666")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload
        assert payload[0]["name"] == "Searchable Barcode Item"


def test_item_search_excludes_archived_items(client, app):
    email = f"barcode_archived_{uuid4().hex[:8]}@example.com"
    _create_user(app, email)

    with app.app_context():
        active_item = Item(name="Active Search Item", base_unit="each")
        archived_item = Item(
            name="Archived Search Item",
            base_unit="each",
            archived=True,
            upc="777777777777",
        )
        db.session.add_all([active_item, archived_item])
        db.session.commit()
        db.session.add(
            ItemUnit(
                item_id=active_item.id,
                name="each",
                factor=1,
                receiving_default=True,
                transfer_default=True,
            )
        )
        db.session.add(
            ItemUnit(
                item_id=archived_item.id,
                name="each",
                factor=1,
                receiving_default=True,
                transfer_default=True,
            )
        )
        db.session.add(ItemBarcode(item_id=archived_item.id, code="888888888888"))
        db.session.commit()
        active_item_id = active_item.id

    with client:
        login(client, email, "pass")

        response = client.get("/items/search?term=Search Item")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload
        assert payload[0]["id"] == active_item_id
        assert payload[0]["name"] == "Active Search Item"
        assert payload[0]["gl_code"] == ""

        response = client.get("/items/search?term=777777777777")
        assert response.status_code == 200
        assert response.get_json() == []

        response = client.get("/items/search?term=888888888888")
        assert response.status_code == 200
        assert response.get_json() == []


def test_item_search_reports_barcode_match_metadata(client, app):
    email = f"barcode_meta_{uuid4().hex[:8]}@example.com"
    _create_user(app, email)

    with app.app_context():
        item = Item(name="Metadata Barcode Item", base_unit="each")
        db.session.add(item)
        db.session.commit()
        db.session.add(
            ItemBarcode(item_id=item.id, code="999999999999")
        )
        db.session.commit()

    with client:
        login(client, email, "pass")
        response = client.get("/items/search?term=999999999999")
        assert response.status_code == 200
        payload = response.get_json()
        assert payload
        assert payload[0]["matched_on"] == "barcode"
        assert payload[0]["exact_match"] is True
