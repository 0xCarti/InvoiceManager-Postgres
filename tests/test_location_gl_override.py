from datetime import date
from contextlib import contextmanager

from flask import template_rendered
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    GLCode,
    Item,
    Location,
    LocationStandItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    User,
    Vendor,
)
from tests.utils import login


@contextmanager
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    template_rendered.connect(record, app)
    try:
        yield recorded
    finally:
        template_rendered.disconnect(record, app)


def test_location_specific_gl_override(client, app):
    with app.app_context():
        user = User(
            email="buyer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Vend", last_name="Or")
        loc = Location(name="Main")
        item_gl = GLCode(code="500000")
        override_gl = GLCode(code="500001")
        item = Item(name="Part", base_unit="each")
        db.session.add_all([user, vendor, loc, item_gl, override_gl, item])
        db.session.flush()
        item.purchase_gl_code_id = item_gl.id
        db.session.add(
            LocationStandItem(
                location_id=loc.id,
                item_id=item.id,
                expected_count=0,
                purchase_gl_code_id=override_gl.id,
            )
        )
        po = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=user.id,
            vendor_name="Vend Or",
            order_date=date.today(),
            expected_date=date.today(),
        )
        db.session.add(po)
        db.session.flush()
        invoice = PurchaseInvoice(
            purchase_order_id=po.id,
            user_id=user.id,
            location_id=loc.id,
            location_name=loc.name,
            vendor_name="Vend Or",
            received_date=date.today(),
        )
        db.session.add(invoice)
        db.session.flush()
        db.session.add(
            PurchaseInvoiceItem(
                invoice_id=invoice.id,
                item_id=item.id,
                item_name=item.name,
                quantity=1,
                cost=10,
            )
        )
        db.session.commit()
        invoice_id = invoice.id
        override_code = override_gl.code
        item_code = item_gl.code
    login(client, "buyer@example.com", "pass")
    with captured_templates(app) as templates:
        resp = client.get(f"/purchase_invoices/{invoice_id}/report")
        assert resp.status_code == 200
        template, context = templates[0]
        report = dict(context["report"])
    assert override_code in report
    assert item_code not in report
