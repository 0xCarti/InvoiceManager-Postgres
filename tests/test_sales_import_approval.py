import json
import os

from app import db
from app.models import (
    Item,
    Location,
    LocationStandItem,
    PosSalesImport,
    PosSalesImportLocation,
    PosSalesImportRow,
    Product,
    ProductRecipeItem,
)
from tests.utils import login


def test_admin_can_approve_sales_import_and_apply_inventory(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        location = Location(name="North Stand")
        item = Item(name="Bun", base_unit="each", quantity=20.0)
        product = Product(name="Hot Dog", price=7.5, cost=2.0)
        db.session.add_all([location, item, product])
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                quantity=2.0,
                countable=True,
            )
        )
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=20.0,
            )
        )

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-approve-1",
            attachment_filename="sales.xls",
            attachment_sha256="a" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="North Stand",
            normalized_location_name="north_stand",
            location_id=location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        row = PosSalesImportRow(
            import_id=sales_import.id,
            location_import_id=import_location.id,
            source_product_name="Hot Dog",
            normalized_product_name="hot_dog",
            product_id=product.id,
            quantity=3.0,
            parse_index=0,
        )
        db.session.add(row)
        db.session.commit()

        sales_import_id = sales_import.id
        row_id = row.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Import approved" in response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        row = db.session.get(PosSalesImportRow, row_id)
        record = LocationStandItem.query.filter_by(
            location_id=sales_import.locations[0].location_id,
            item_id=ProductRecipeItem.query.filter_by(product_id=row.product_id).first().item_id,
        ).first()
        item = Item.query.filter_by(name="Bun").first()

        assert sales_import.status == "approved"
        assert sales_import.approved_by is not None
        assert sales_import.approved_at is not None
        assert sales_import.approval_batch_id
        assert row.approval_batch_id == sales_import.approval_batch_id
        assert record is not None
        assert record.expected_count == 14.0
        assert item.quantity == 14.0

        metadata = json.loads(row.approval_metadata)
        assert metadata["approval_batch_id"] == sales_import.approval_batch_id
        assert len(metadata["changes"]) == 1
        change = metadata["changes"][0]
        assert change["expected_count_before"] == 20.0
        assert change["expected_count_after"] == 14.0
        assert change["item_quantity_before"] == 20.0
        assert change["item_quantity_after"] == 14.0
        assert change["consumed_quantity"] == 6.0


def test_sales_import_approval_blocked_for_unresolved_mappings(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-approve-2",
            attachment_filename="sales.xls",
            attachment_sha256="b" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Unmapped",
            normalized_location_name="unmapped",
            location_id=None,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        db.session.add(
            PosSalesImportRow(
                import_id=sales_import.id,
                location_import_id=import_location.id,
                source_product_name="Unknown",
                normalized_product_name="unknown",
                product_id=None,
                quantity=1.0,
                parse_index=0,
            )
        )
        db.session.commit()
        sales_import_id = sales_import.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Approval blocked" in response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        assert sales_import.status in {"pending", "needs_mapping"}
        assert sales_import.approved_at is None


def test_admin_can_undo_approved_sales_import_and_restore_inventory(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        location = Location(name="South Stand")
        item = Item(name="Patty", base_unit="each", quantity=30.0)
        product = Product(name="Burger", price=9.5, cost=3.0)
        db.session.add_all([location, item, product])
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                quantity=2.0,
                countable=True,
            )
        )
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=30.0,
            )
        )

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-undo-1",
            attachment_filename="sales.xls",
            attachment_sha256="c" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="South Stand",
            normalized_location_name="south_stand",
            location_id=location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        row = PosSalesImportRow(
            import_id=sales_import.id,
            location_import_id=import_location.id,
            source_product_name="Burger",
            normalized_product_name="burger",
            product_id=product.id,
            quantity=4.0,
            parse_index=0,
        )
        db.session.add(row)
        db.session.commit()
        sales_import_id = sales_import.id

    with client:
        login(client, admin_email, admin_pass)
        approve_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"Import approved" in approve_response.data

        undo_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "undo_approved_import",
                "reversal_reason": "Uploaded duplicate attachment",
            },
            follow_redirects=True,
        )
        assert undo_response.status_code == 200
        assert b"Import reversal complete" in undo_response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        row = sales_import.rows[0]
        record = LocationStandItem.query.filter_by(
            location_id=sales_import.locations[0].location_id,
            item_id=ProductRecipeItem.query.filter_by(product_id=row.product_id).first().item_id,
        ).first()
        item = Item.query.filter_by(name="Patty").first()

        assert sales_import.status == "reversed"
        assert sales_import.reversed_by is not None
        assert sales_import.reversed_at is not None
        assert sales_import.reversal_batch_id
        assert sales_import.reversal_reason == "Uploaded duplicate attachment"
        assert row.reversal_batch_id == sales_import.reversal_batch_id
        assert record is not None
        assert record.expected_count == 30.0
        assert item.quantity == 30.0

        metadata = json.loads(row.approval_metadata)
        assert metadata["reversal"]["reversal_batch_id"] == sales_import.reversal_batch_id
        assert metadata["reversal"]["reason"] == "Uploaded duplicate attachment"
        assert len(metadata["reversal"]["changes"]) == 1


def test_sales_import_undo_blocked_unless_import_is_approved(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-undo-2",
            attachment_filename="sales.xls",
            attachment_sha256="d" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.commit()
        sales_import_id = sales_import.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "undo_approved_import",
                "reversal_reason": "Should not be allowed",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Undo is only allowed when the import status is Approved." in response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        assert sales_import.status == "pending"
        assert sales_import.reversed_at is None


def test_sales_import_detail_shows_undo_negative_inventory_warnings(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        location = Location(name="Warning Stand")
        item = Item(name="Soda Syrup", base_unit="each", quantity=1.0)
        product = Product(name="Soda", price=3.0, cost=0.5)
        db.session.add_all([location, item, product])
        db.session.flush()

        stand_item = LocationStandItem(
            location_id=location.id,
            item_id=item.id,
            expected_count=1.0,
        )
        db.session.add(stand_item)

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-undo-3",
            attachment_filename="sales.xls",
            attachment_sha256="e" * 64,
            status="approved",
            approval_batch_id="batch-1",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Warning Stand",
            normalized_location_name="warning_stand",
            location_id=location.id,
            parse_index=0,
            approval_batch_id="batch-1",
        )
        db.session.add(import_location)
        db.session.flush()

        db.session.add(
            PosSalesImportRow(
                import_id=sales_import.id,
                location_import_id=import_location.id,
                source_product_name="Soda",
                normalized_product_name="soda",
                product_id=product.id,
                quantity=1.0,
                parse_index=0,
                approval_batch_id="batch-1",
                approval_metadata=json.dumps(
                    {
                        "approval_batch_id": "batch-1",
                        "changes": [
                            {
                                "item_id": item.id,
                                "location_id": location.id,
                                "location_stand_item_id": stand_item.id,
                                "consumed_quantity": -5.0,
                            }
                        ],
                    }
                ),
            )
        )
        db.session.commit()
        sales_import_id = sales_import.id
        location_id = location.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.get(
            f"/controlpanel/sales-imports/{sales_import_id}",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Potential negative inventory impact" in response.data


def test_admin_can_soft_delete_sales_import(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-delete-1",
            attachment_filename="sales.xls",
            attachment_sha256="f" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.commit()
        sales_import_id = sales_import.id

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "delete_import",
                "deletion_reason": "Duplicate staging run",
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Import marked as deleted" in response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        assert sales_import is not None
        assert sales_import.status == "deleted"
        assert sales_import.deleted_by is not None
        assert sales_import.deleted_at is not None
        assert sales_import.deletion_reason == "Duplicate staging run"


def test_sales_import_approval_applies_stock_changes_only_once(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        location = Location(name="Once Stand")
        item = Item(name="Once Bun", base_unit="each", quantity=10.0)
        product = Product(name="Once Dog", price=7.0, cost=2.0)
        db.session.add_all([location, item, product])
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                quantity=1.0,
                countable=True,
            )
        )
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=10.0,
            )
        )

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-once-approve",
            attachment_filename="sales.xls",
            attachment_sha256="e" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Once Stand",
            normalized_location_name="once_stand",
            location_id=location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        db.session.add(
            PosSalesImportRow(
                import_id=sales_import.id,
                location_import_id=import_location.id,
                source_product_name="Once Dog",
                normalized_product_name="once_dog",
                product_id=product.id,
                quantity=4.0,
                parse_index=0,
            )
        )
        db.session.commit()
        sales_import_id = sales_import.id

    with client:
        login(client, admin_email, admin_pass)
        first = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert first.status_code == 200
        assert b"Import approved" in first.data

        second = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert second.status_code == 200
        assert b"only allowed while the import status is Pending" in second.data

    with app.app_context():
        item = Item.query.filter_by(name="Once Bun").one()
        location = Location.query.filter_by(name="Once Stand").one()
        stand_item = LocationStandItem.query.filter_by(
            location_id=location.id,
            item_id=item.id,
        ).one()
        assert item.quantity == 6.0
        assert stand_item.expected_count == 6.0


def test_sales_import_undo_applies_reversal_only_once(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        location = Location(name="Undo Once Stand")
        item = Item(name="Undo Once Patty", base_unit="each", quantity=8.0)
        product = Product(name="Undo Once Burger", price=9.0, cost=3.0)
        db.session.add_all([location, item, product])
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                quantity=1.0,
                countable=True,
            )
        )
        db.session.add(
            LocationStandItem(
                location_id=location.id,
                item_id=item.id,
                expected_count=8.0,
            )
        )

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-once-undo",
            attachment_filename="sales.xls",
            attachment_sha256="f" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Undo Once Stand",
            normalized_location_name="undo_once_stand",
            location_id=location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        db.session.add(
            PosSalesImportRow(
                import_id=sales_import.id,
                location_import_id=import_location.id,
                source_product_name="Undo Once Burger",
                normalized_product_name="undo_once_burger",
                product_id=product.id,
                quantity=3.0,
                parse_index=0,
            )
        )
        db.session.commit()
        sales_import_id = sales_import.id
        location_id = location.id

    with client:
        login(client, admin_email, admin_pass)
        client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        first_undo = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "undo_approved_import",
                "reversal_reason": "undo once",
                "confirm_reversal": "1",
            },
            follow_redirects=True,
        )
        assert first_undo.status_code == 200
        assert b"Import reversal complete" in first_undo.data

        second_undo = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "undo_approved_import",
                "reversal_reason": "undo twice",
                "confirm_reversal": "1",
            },
            follow_redirects=True,
        )
        assert second_undo.status_code == 200
        assert b"Undo is only allowed when the import status is Approved" in second_undo.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        item = Item.query.filter_by(name="Undo Once Patty").one()
        stand_item = LocationStandItem.query.filter_by(
            location_id=location_id,
            item_id=item.id,
        ).one()

        assert sales_import.status == "reversed"
        assert sales_import.reversed_by is not None
        assert sales_import.reversed_at is not None
        assert sales_import.reversal_reason == "undo once"
        assert item.quantity == 8.0
        assert stand_item.expected_count == 8.0
