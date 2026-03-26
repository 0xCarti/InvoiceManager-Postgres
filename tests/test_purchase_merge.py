import datetime
import json
import sys
import types

import pytest
from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    ActivityLog,
    Item,
    ItemUnit,
    Location,
    PurchaseInvoiceDraft,
    PurchaseOrder,
    PurchaseOrderItem,
    User,
    Vendor,
)
from app.services.purchase_merge import merge_purchase_orders, PurchaseMergeError
from app.utils.activity import flush_activity_logs
from tests.utils import login

sys.modules.setdefault("pypdf", types.SimpleNamespace(PdfMerger=object))


def _create_user_vendor_and_items(app):
    with app.app_context():
        user = User(
            email="merge@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        vendor = Vendor(first_name="Merge", last_name="Vendor")
        item_a = Item(name="Item A", base_unit="each")
        item_b = Item(name="Item B", base_unit="each")
        unit_a = ItemUnit(item=item_a, name="each", factor=1, receiving_default=True)
        unit_b = ItemUnit(item=item_b, name="each", factor=1, receiving_default=True)
        location = Location(name="Receiving Bay")
        db.session.add_all([user, vendor, item_a, item_b, unit_a, unit_b, location])
        db.session.commit()
        return (
            user.email,
            vendor.id,
            item_a.id,
            item_b.id,
            unit_a.id,
            unit_b.id,
            location.id,
        )


def test_merge_purchase_orders_merges_invoice_drafts(client, app):
    (
        user_email,
        vendor_id,
        item_a_id,
        item_b_id,
        unit_a_id,
        unit_b_id,
        _,
    ) = _create_user_vendor_and_items(app)

    with app.app_context():
        user_id = User.query.filter_by(email=user_email).first().id
        target_order = PurchaseOrder(
            vendor_id=vendor_id,
            user_id=user_id,
            vendor_name="Merge Vendor",
            order_date=datetime.date(2024, 5, 1),
            expected_date=datetime.date(2024, 5, 3),
            delivery_charge=1.0,
            received=False,
        )
        source_order = PurchaseOrder(
            vendor_id=vendor_id,
            user_id=user_id,
            vendor_name="Merge Vendor",
            order_date=datetime.date(2024, 5, 2),
            expected_date=datetime.date(2024, 5, 3),
            delivery_charge=2.0,
            received=False,
        )
        db.session.add_all([target_order, source_order])
        db.session.commit()

        target_id = target_order.id
        source_id = source_order.id

        db.session.add_all(
            [
                PurchaseOrderItem(
                    purchase_order_id=target_id,
                    item_id=item_a_id,
                    unit_id=unit_a_id,
                    quantity=2,
                    unit_cost=1.25,
                    position=0,
                ),
                PurchaseOrderItem(
                    purchase_order_id=source_id,
                    item_id=item_b_id,
                    unit_id=unit_b_id,
                    quantity=4,
                    unit_cost=2.5,
                    position=0,
                ),
            ]
        )
        db.session.commit()

        db.session.add_all(
            [
                PurchaseInvoiceDraft(
                    purchase_order_id=target_id,
                    payload=json.dumps(
                        {
                            "invoice_number": "T-INV",
                            "items": [
                                {
                                    "item_id": item_a_id,
                                    "unit_id": unit_a_id,
                                    "quantity": 2,
                                    "cost": 1.25,
                                    "position": 0,
                                }
                            ],
                        }
                    ),
                ),
                PurchaseInvoiceDraft(
                    purchase_order_id=source_id,
                    payload=json.dumps(
                        {
                            "invoice_number": "T-INV",
                            "items": [
                                {
                                    "item_id": item_b_id,
                                    "unit_id": unit_b_id,
                                    "quantity": 4,
                                    "cost": 2.5,
                                    "position": 0,
                                }
                            ],
                        }
                    ),
                ),
            ]
        )
        db.session.commit()

    with app.app_context():
        db.session.rollback()
        db.session.remove()
        merge_purchase_orders(target_id, [source_id], require_expected_date_match=False)
        flush_activity_logs()

        merged_po = db.session.get(PurchaseOrder, target_id)
        assert len(merged_po.items) == 2
        assert {item.position for item in merged_po.items} == {0, 1}

        target_draft = PurchaseInvoiceDraft.query.filter_by(
            purchase_order_id=target_id
        ).first()
        assert target_draft is not None
        payload = target_draft.data
        positions = {item["position"] for item in payload.get("items", [])}
        assert positions == {0, 1}
        assert {item["item_id"] for item in payload["items"]} == {item_a_id, item_b_id}

        assert (
            PurchaseInvoiceDraft.query.filter_by(purchase_order_id=source_id).first()
            is None
        )

        log_message = (
            db.session.query(ActivityLog.activity)
            .order_by(ActivityLog.timestamp.desc())
            .first()
            .activity
        )
        assert str(source_id) in log_message

        db.session.refresh(merged_po)
        assert merged_po.delivery_charge == 3.0

    with client:
        login(client, user_email, "pass")
        resp = client.get(f"/purchase_orders/{merged_po.id}/receive")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)
        assert f'value="{item_a_id}"' in html
        assert f'value="{item_b_id}"' in html


def test_merge_purchase_order_draft_conflict_raises(app):
    (
        user_email,
        vendor_id,
        item_a_id,
        item_b_id,
        unit_a_id,
        unit_b_id,
        _,
    ) = _create_user_vendor_and_items(app)

    with app.app_context():
        target_order = PurchaseOrder(
            vendor_id=vendor_id,
            user_id=User.query.filter_by(email=user_email).first().id,
            vendor_name="Merge Vendor",
            order_date=datetime.date(2024, 6, 1),
            expected_date=datetime.date(2024, 6, 2),
            delivery_charge=0.0,
            received=False,
        )
        source_order = PurchaseOrder(
            vendor_id=vendor_id,
            user_id=User.query.filter_by(email=user_email).first().id,
            vendor_name="Merge Vendor",
            order_date=datetime.date(2024, 6, 1),
            expected_date=datetime.date(2024, 6, 2),
            delivery_charge=0.0,
            received=False,
        )
        db.session.add_all([target_order, source_order])
        db.session.commit()

        target_id = target_order.id
        source_id = source_order.id

        db.session.add_all(
            [
                PurchaseOrderItem(
                    purchase_order_id=target_order.id,
                    item_id=item_a_id,
                    unit_id=unit_a_id,
                    quantity=1,
                    position=0,
                ),
                PurchaseOrderItem(
                    purchase_order_id=source_order.id,
                    item_id=item_b_id,
                    unit_id=unit_b_id,
                    quantity=1,
                    position=0,
                ),
                PurchaseInvoiceDraft(
                    purchase_order_id=target_order.id,
                    payload=json.dumps({"invoice_number": "A", "items": []}),
                ),
                PurchaseInvoiceDraft(
                    purchase_order_id=source_order.id,
                    payload=json.dumps({"invoice_number": "B", "items": []}),
                ),
            ]
        )
        db.session.commit()

    with app.app_context():
        db.session.rollback()
        db.session.remove()
        with pytest.raises(PurchaseMergeError):
            merge_purchase_orders(target_id, [source_id])
