from datetime import date
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    Customer,
    Invoice,
    InvoiceProduct,
    Item,
    ItemUnit,
    Location,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceItem,
    PurchaseOrder,
    Transfer,
    TransferItem,
    User,
    Vendor,
    VendorItemAlias,
)
from tests.permission_helpers import grant_item_workflow_permissions, grant_permissions
from tests.utils import login


def setup_history(app, *, include_vendor_alias_permission: bool = True):
    with app.app_context():
        user = User(
            email="hist@example.com",
            password=generate_password_hash("pass"),
            is_admin=False,
            active=True,
        )
        customer = Customer(first_name="Cust", last_name="Omer")
        vendor = Vendor(first_name="Vend", last_name="Or")
        item = Item(name="Widget", base_unit="each", cost=10)
        unit = ItemUnit(item=item, name="each", factor=1, receiving_default=True, transfer_default=True)
        loc1 = Location(name="L1")
        loc2 = Location(name="L2")
        product = Product(name="WidgetProd", gl_code="5000", price=5, cost=0)
        secondary_product = Product(
            name="WidgetMixer", gl_code="5001", price=6, cost=0
        )
        pri = ProductRecipeItem(
            product=product, item=item, quantity=1, countable=True
        )
        secondary_pri = ProductRecipeItem(
            product=secondary_product, item=item, quantity=1, countable=False
        )
        db.session.add_all(
            [
                user,
                customer,
                vendor,
                item,
                unit,
                loc1,
                loc2,
                product,
                secondary_product,
                pri,
                secondary_pri,
            ]
        )
        db.session.commit()
        grant_item_workflow_permissions(user)
        if include_vendor_alias_permission:
            grant_permissions(
                user,
                "vendor_item_aliases.view",
                group_name=f"Item Alias View {user.email}",
                description="Can view vendor alias details from item pages.",
            )
        po = PurchaseOrder(vendor_id=vendor.id, user_id=user.id, vendor_name="Vend Or", order_date=date.today(), expected_date=date.today(), delivery_charge=0, received=True)
        db.session.add(po)
        db.session.commit()
        db.session.add(
            VendorItemAlias(
                vendor_id=vendor.id,
                item_id=item.id,
                item_unit_id=unit.id,
                vendor_sku="VEN-100",
                vendor_description="Widget Master Case",
                normalized_description="widget master case",
                pack_size="12 each",
                default_cost=18.5,
            )
        )
        pi = PurchaseInvoice(purchase_order_id=po.id, user_id=user.id, location_id=loc1.id, vendor_name="Vend Or", location_name=loc1.name, received_date=date.today(), invoice_number="PI1", gst=0, pst=0, delivery_charge=0)
        pii = PurchaseInvoiceItem(invoice=pi, item=item, item_name=item.name, unit=unit, unit_name=unit.name, quantity=5, cost=2.5)
        inv = Invoice(id="INV1", user_id=user.id, customer_id=customer.id)
        ip = InvoiceProduct(invoice=inv, product=product, product_name=product.name, quantity=2, unit_price=5, line_subtotal=10, line_gst=0, line_pst=0)
        transfer = Transfer(from_location_id=loc1.id, to_location_id=loc2.id, user_id=user.id, from_location_name=loc1.name, to_location_name=loc2.name)
        ti = TransferItem(transfer=transfer, item=item, item_name=item.name, quantity=3)
        db.session.add_all([pi, pii, inv, ip, transfer, ti])
        db.session.commit()
        return user.email, item.id, pi.id, inv.id, transfer.id


def test_item_detail_page(client, app):
    email, item_id, pi_id, inv_id, transfer_id = setup_history(app)
    with client:
        login(client, email, "pass")
        resp = client.get("/items")
        text = resp.get_data(as_text=True)
        assert f"/items/{item_id}" in text
        resp = client.get(f"/items/{item_id}")
        page = resp.get_data(as_text=True)
        assert str(pi_id) in page
        assert inv_id in page
        assert str(transfer_id) in page
        assert "WidgetProd" in page
        assert "WidgetMixer" in page
        assert "Product Recipe Usage" in page
        assert "Countable" in page
        assert "Not Countable" in page
        assert "VEN-100" in page
        assert "Widget Master Case" in page


def test_item_detail_page_hides_vendor_aliases_without_permission(client, app):
    email, item_id, *_ = setup_history(app, include_vendor_alias_permission=False)

    with client:
        login(client, email, "pass")
        resp = client.get(f"/items/{item_id}")

    assert resp.status_code == 200
    page = resp.get_data(as_text=True)
    assert "Vendor SKU Mappings" not in page
    assert "VEN-100" not in page
