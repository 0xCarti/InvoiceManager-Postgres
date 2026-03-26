from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    Invoice,
    Item,
    ItemUnit,
    Location,
    LocationStandItem,
    Product,
    ProductRecipeItem,
    Transfer,
    TransferItem,
    User,
)
from tests.utils import login


def create_user(app, email="user@example.com"):
    with app.app_context():
        user = User(
            email=email, password=generate_password_hash("pass"), active=True
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def test_item_lifecycle(client, app):
    create_user(app, "itemuser@example.com")

    with client:
        login(client, "itemuser@example.com", "pass")
        resp = client.post(
            "/items/add",
            data={
                "name": "Widget",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        item = Item.query.filter_by(name="Widget").first()
        assert item is not None
        item_id = item.id

    with client:
        login(client, "itemuser@example.com", "pass")
        resp = client.post(
            f"/items/edit/{item_id}",
            data={
                "name": "Gadget",
                "base_unit": "each",
                "gl_code": "5000",
                "units-0-name": "each",
                "units-0-factor": 1,
                "units-0-receiving_default": "y",
                "units-0-transfer_default": "y",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        updated = db.session.get(Item, item_id)
        assert updated.name == "Gadget"

    with client:
        login(client, "itemuser@example.com", "pass")
        resp = client.post(f"/items/delete/{item_id}", follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Item, item_id).archived


def test_transfer_flow(client, app):
    user_id = create_user(app, "transfer@example.com")
    with app.app_context():
        loc1 = Location(name="A")
        loc2 = Location(name="B")
        item = Item(name="Thing", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.commit()
        loc1_id, loc2_id, item_id = loc1.id, loc2.id, item.id
        unit_id = unit.id

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 5,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        assert transfer is not None
        ti = TransferItem.query.filter_by(transfer_id=transfer.id).first()
        assert ti.item_id == item_id and ti.quantity == 5
        tid = transfer.id

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.get(f"/transfers/complete/{tid}")
        assert resp.status_code == 200
        assert b"Confirm Transfer Completion" in resp.data
        resp = client.post(f"/transfers/complete/{tid}", follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Transfer, tid).completed

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.get(
            f"/transfers/uncomplete/{tid}", follow_redirects=True
        )
        assert resp.status_code == 200

    with app.app_context():
        assert not db.session.get(Transfer, tid).completed

    with client:
        login(client, "transfer@example.com", "pass")
        resp = client.post(f"/transfers/delete/{tid}", follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        assert db.session.get(Transfer, tid) is None


def test_ajax_edit_transfer_updates_quantity(client, app):
    user_id = create_user(app, "ajaxedit@example.com")
    with app.app_context():
        loc1 = Location(name="AjaxFrom")
        loc2 = Location(name="AjaxTo")
        item = Item(name="AjaxThing", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.commit()
        loc1_id, loc2_id, item_id = loc1.id, loc2.id, item.id
        unit_id = unit.id

    transfer_id = None
    with client:
        login(client, "ajaxedit@example.com", "pass")
        add_resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 3,
            },
            follow_redirects=True,
        )
        assert add_resp.status_code == 200

        with app.app_context():
            transfer = Transfer.query.filter_by(user_id=user_id).first()
            assert transfer is not None
            transfer_id = transfer.id

        edit_resp = client.post(
            f"/transfers/ajax_edit/{transfer_id}",
            data={
                "edit-from_location_id": loc1_id,
                "edit-to_location_id": loc2_id,
                "edit-items-0-item": item_id,
                "edit-items-0-unit": unit_id,
                "edit-items-0-quantity": 7,
            },
        )
        assert edit_resp.status_code == 200
        payload = edit_resp.get_json()
        assert payload["success"] is True

    with app.app_context():
        updated_transfer = db.session.get(Transfer, transfer_id)
        assert updated_transfer is not None
        assert len(updated_transfer.transfer_items) == 1
        assert updated_transfer.transfer_items[0].quantity == 7


def test_invoice_creation_total(client, app):
    create_user(app, "invoice@example.com")
    with app.app_context():
        customer = Customer(first_name="John", last_name="Doe")
        product = Product(name="Widget", price=10.0, cost=5.0)
        db.session.add_all([customer, product])
        db.session.commit()
        cust_id = customer.id

    with client:
        login(client, "invoice@example.com", "pass")
        resp = client.post(
            "/create_invoice",
            data={"customer": float(cust_id), "products": "Widget?2??"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert b"Invoice created successfully" in resp.data

    with app.app_context():
        invoice = Invoice.query.filter_by(customer_id=cust_id).first()
        assert invoice is not None
        assert len(invoice.products) == 1
        assert invoice.products[0].quantity == 2
        assert round(invoice.total, 2) == 22.4


def test_transfer_expected_counts_updated(client, app):
    user_id = create_user(app, "expected@example.com")
    with app.app_context():
        loc1 = Location(name="L1")
        loc2 = Location(name="L2")
        item = Item(name="Countable", base_unit="each")
        db.session.add_all([loc1, loc2, item])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        lsi1 = LocationStandItem(
            location_id=loc1.id, item_id=item.id, expected_count=0
        )
        lsi2 = LocationStandItem(
            location_id=loc2.id, item_id=item.id, expected_count=0
        )
        db.session.add_all([lsi1, lsi2])
        db.session.commit()
        loc1_id, loc2_id, item_id, unit_id = loc1.id, loc2.id, item.id, unit.id

    with client:
        login(client, "expected@example.com", "pass")
        resp = client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 4,
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        tid = transfer.id

    with client:
        login(client, "expected@example.com", "pass")
        resp = client.get(f"/transfers/complete/{tid}")
        assert resp.status_code == 200
        assert b"Confirm Transfer Completion" in resp.data
        resp = client.post(f"/transfers/complete/{tid}", follow_redirects=True)
        assert resp.status_code == 200

    with app.app_context():
        l1 = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        l2 = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        assert l1.expected_count == -4
        assert l2.expected_count == 4

    with client:
        login(client, "expected@example.com", "pass")
        resp = client.get(
            f"/transfers/uncomplete/{tid}", follow_redirects=True
        )
        assert resp.status_code == 200

    with app.app_context():
        l1 = LocationStandItem.query.filter_by(
            location_id=loc1_id, item_id=item_id
        ).first()
        l2 = LocationStandItem.query.filter_by(
            location_id=loc2_id, item_id=item_id
        ).first()
        assert l1.expected_count == 0
        assert l2.expected_count == 0


def test_transfer_item_form_nested_csrf_disabled(app):
    with app.test_request_context('/'):
        from app.forms import TransferForm

        form = TransferForm(prefix='add')
        entry = form.items.entries[0]

        # Nested transfer item forms should not require CSRF tokens so that
        # dynamically added rows from JavaScript post successfully.
        assert not hasattr(entry, 'csrf_token')


def test_stand_sheet_shows_expected_counts(client, app):
    user_id = create_user(app, "stand@example.com")
    with app.app_context():
        loc1 = Location(name="SL1")
        loc2 = Location(name="SL2")
        item = Item(name="StandItem", base_unit="each")
        product = Product(name="StandProd", price=1.0, cost=0.5)
        db.session.add_all([loc1, loc2, item, product])
        db.session.commit()
        unit = ItemUnit(
            item_id=item.id,
            name="each",
            factor=1,
            receiving_default=True,
            transfer_default=True,
        )
        db.session.add(unit)
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=loc1.id, item_id=item.id, expected_count=0
                ),
                LocationStandItem(
                    location_id=loc2.id, item_id=item.id, expected_count=0
                ),
            ]
        )
        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                unit_id=unit.id,
                quantity=1,
                countable=True,
            )
        )
        loc1.products.append(product)
        loc2.products.append(product)
        db.session.commit()
        loc1_id, loc2_id, item_id, unit_id = loc1.id, loc2.id, item.id, unit.id

    with client:
        login(client, "stand@example.com", "pass")
        client.post(
            "/transfers/add",
            data={
                "from_location_id": loc1_id,
                "to_location_id": loc2_id,
                "items-0-item": item_id,
                "items-0-unit": unit_id,
                "items-0-quantity": 5,
            },
            follow_redirects=True,
        )

    with app.app_context():
        transfer = Transfer.query.filter_by(user_id=user_id).first()
        tid = transfer.id

    with client:
        login(client, "stand@example.com", "pass")
        resp_confirm = client.get(f"/transfers/complete/{tid}")
        assert b"Confirm Transfer Completion" in resp_confirm.data
        client.post(f"/transfers/complete/{tid}", follow_redirects=True)
        resp1 = client.get(f"/locations/{loc1_id}/stand_sheet")
        resp2 = client.get(f"/locations/{loc2_id}/stand_sheet")
        assert b"-5" in resp1.data
        assert b"5" in resp2.data
