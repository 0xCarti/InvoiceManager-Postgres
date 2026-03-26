from datetime import date, datetime

from werkzeug.security import generate_password_hash

from app import create_admin_user, db
from app.models import (
    GLCode,
    Item,
    Location,
    LocationStandItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    Transfer,
    TransferItem,
    User,
    Vendor,
)


def ensure_admin_user():
    admin = User.query.filter_by(email="admin@example.com").first()
    if admin is None:
        create_admin_user()
        admin = User.query.filter_by(email="admin@example.com").first()
    if admin is None:
        admin = User(
            email="admin@example.com",
            password=generate_password_hash("adminpass"),
            active=True,
            is_admin=True,
        )
        db.session.add(admin)
        db.session.commit()
    return admin


def login_admin(client, app):
    with app.app_context():
        admin = ensure_admin_user()
        admin_id = admin.id
    with client.session_transaction() as session:
        session["_user_id"] = str(admin_id)
        session["_fresh"] = True


def test_inventory_variance_report_includes_spoilage(client, app):
    with app.app_context():
        admin = ensure_admin_user()

        gl_code = GLCode.query.filter_by(code="5000").first()
        if gl_code is None:
            gl_code = GLCode(code="5000", description="Food Purchases")
            db.session.add(gl_code)
            db.session.commit()

        source = Location(name="Kitchen", is_spoilage=False)
        dest = Location(name="Waste", is_spoilage=True)
        item = Item(name="Milk", base_unit="each", cost=2.5)
        vendor = Vendor(first_name="Acme", last_name="Foods")
        purchase_order = PurchaseOrder(
            vendor=vendor,
            user_id=admin.id,
            vendor_name="Acme Foods",
            order_date=date(2024, 1, 1),
            expected_date=date(2024, 1, 2),
        )
        invoice = PurchaseInvoice(
            purchase_order=purchase_order,
            user_id=admin.id,
            location=source,
            vendor_name="Acme Foods",
            location_name="Kitchen",
            received_date=date(2024, 1, 3),
        )
        invoice_item = PurchaseInvoiceItem(
            invoice=invoice,
            item=item,
            item_name="Milk",
            quantity=10,
            cost=2.5,
            purchase_gl_code=gl_code,
        )
        stand_item = LocationStandItem(
            location=source,
            item=item,
            purchase_gl_code=gl_code,
        )
        transfer = Transfer(
            from_location=source,
            to_location=dest,
            user_id=admin.id,
            from_location_name="Kitchen",
            to_location_name="Waste",
            date_created=datetime(2024, 1, 5, 12, 0, 0),
            completed=True,
        )
        transfer_item = TransferItem(
            transfer=transfer,
            item=item,
            item_name="Milk",
            quantity=2,
        )

        db.session.add_all(
            [
                source,
                dest,
                item,
                vendor,
                purchase_order,
                invoice,
                invoice_item,
                stand_item,
                transfer,
                transfer_item,
            ]
        )
        db.session.commit()

        item_id = item.id
        gl_id = gl_code.id

    login_admin(client, app)
    response = client.post(
        "/reports/inventory-variance",
        data={
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "items": [str(item_id)],
            "gl_codes": [str(gl_id)],
        },
    )

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Spoilage" in html
    assert "2.00" in html
    assert "$5.00" in html
