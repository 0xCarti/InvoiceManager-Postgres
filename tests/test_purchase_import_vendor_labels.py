from app import db
from app.models import Setting, Vendor
from app.routes.purchase_routes import _get_enabled_import_vendors


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
