from app import db
from app.models import Setting, Vendor
from app.routes.purchase_routes import _get_enabled_import_vendors
from tests.utils import login
from werkzeug.security import generate_password_hash
from app.models import User


def test_enabled_import_vendor_matches_corporate_suffix(app):
    with app.app_context():
        Setting.set_enabled_purchase_import_vendors(["CENTRAL SUPPLY"])
        vendor = Vendor(first_name="Central", last_name="Supply Ltd")
        db.session.add(vendor)
        db.session.commit()

        enabled_vendors = _get_enabled_import_vendors()

        assert vendor in enabled_vendors


def test_enabled_import_vendor_matches_first_last_part(app):
    with app.app_context():
        Setting.set_enabled_purchase_import_vendors(["FRESH MARKET"])
        vendor = Vendor(first_name="Fresh", last_name="Market/Canada")
        db.session.add(vendor)
        db.session.commit()

        enabled_vendors = _get_enabled_import_vendors()

        assert vendor in enabled_vendors


def test_enabled_import_vendor_matches_manitoba_liquor_and_lotteries(app):
    with app.app_context():
        Setting.set_enabled_purchase_import_vendors(
            ["MANITOBA LIQUOR & LOTTERIES"]
        )
        vendor = Vendor(first_name="Manitoba", last_name="Liquor & Lotteries Ltd")
        db.session.add(vendor)
        db.session.commit()

        enabled_vendors = _get_enabled_import_vendors()

        assert vendor in enabled_vendors


def test_purchase_order_upload_modal_lists_manitoba_vendor_when_enabled(client, app):
    with app.app_context():
        user = User(
            email="manitoba-upload@example.com",
            password=generate_password_hash("pass"),
            is_admin=True,
            active=True,
        )
        vendor = Vendor(first_name="Manitoba", last_name="Liquor & Lotteries")
        db.session.add_all([user, vendor])
        Setting.set_enabled_purchase_import_vendors(
            ["MANITOBA LIQUOR & LOTTERIES"]
        )
        db.session.commit()

    with client:
        login(client, "manitoba-upload@example.com", "pass")
        response = client.get("/purchase_orders")

    assert response.status_code == 200
    assert "Manitoba Liquor &amp; Lotteries" in response.get_data(as_text=True)
