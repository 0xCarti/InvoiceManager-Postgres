import json
import os
import re
from datetime import date as date_cls, datetime

from app import db
from app.models import (
    Event,
    EventLocation,
    EventLocationTerminalSalesSummary,
    Item,
    Location,
    LocationStandItem,
    PosSalesImport,
    PosSalesImportLocation,
    PosSalesImportRow,
    Product,
    ProductRecipeItem,
    TerminalSale,
    User,
    UserFilterPreference,
)
from tests.permission_helpers import grant_permissions
from tests.utils import login
from werkzeug.security import generate_password_hash


def _create_price_review_import(
    app,
    *,
    message_id: str,
    product_price: float = 4.0,
    file_price: float = 5.0,
    quantity: float = 2.0,
):
    with app.app_context():
        location = Location(name=f"Price Stand {message_id}")
        item = Item(name=f"Price Item {message_id}", base_unit="each", quantity=10.0)
        product = Product(name=f"Price Product {message_id}", price=product_price, cost=1.0)
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
            message_id=message_id,
            attachment_filename="sales.xls",
            attachment_sha256=(message_id[-1] or "p") * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name=location.name,
            normalized_location_name=location.name.lower().replace(" ", "_"),
            location_id=location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        row = PosSalesImportRow(
            import_id=sales_import.id,
            location_import_id=import_location.id,
            source_product_name=product.name,
            normalized_product_name=product.name.lower().replace(" ", "_"),
            product_id=product.id,
            quantity=quantity,
            computed_unit_price=file_price,
            discount_raw="-1.25",
            discount_abs=1.25,
            parse_index=0,
        )
        db.session.add(row)
        db.session.commit()

        return {
            "import_id": sales_import.id,
            "location_import_id": import_location.id,
            "row_id": row.id,
            "product_id": product.id,
            "item_id": item.id,
            "location_id": location.id,
        }


def _create_unresolved_sales_import(app, *, message_id: str):
    with app.app_context():
        location = Location(name=f"Unresolved Stand {message_id}")
        product = Product(name=f"Unresolved Product {message_id}", price=4.0, cost=1.0)
        db.session.add_all([location, product])
        db.session.flush()

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id=message_id,
            attachment_filename="sales.xls",
            attachment_sha256=(message_id[-1] or "u") * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name=location.name,
            normalized_location_name=location.name.lower().replace(" ", "_"),
            location_id=location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        row = PosSalesImportRow(
            import_id=sales_import.id,
            location_import_id=import_location.id,
            source_product_name=product.name,
            normalized_product_name=product.name.lower().replace(" ", "_"),
            product_id=None,
            quantity=2.0,
            parse_index=0,
        )
        db.session.add(row)
        db.session.commit()

        return {
            "import_id": sales_import.id,
            "location_import_id": import_location.id,
            "row_id": row.id,
        }


def _create_event_linked_sales_import(
    app,
    *,
    message_id: str,
    sales_quantity: float = 3.0,
    existing_event_quantity: float | None = None,
):
    with app.app_context():
        sales_date = date_cls(2026, 4, 15)
        location = Location(name=f"Event Stand {message_id}")
        item = Item(name=f"Event Item {message_id}", base_unit="each", quantity=25.0)
        product = Product(name=f"Event Product {message_id}", price=7.5, cost=2.0)
        event = Event(
            name=f"Event {message_id}",
            start_date=sales_date,
            end_date=sales_date,
        )
        db.session.add_all([location, item, product, event])
        db.session.flush()

        event_location = EventLocation(event_id=event.id, location_id=location.id)
        db.session.add(event_location)
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
                expected_count=25.0,
            )
        )

        if existing_event_quantity is not None:
            db.session.add(
                TerminalSale(
                    event_location_id=event_location.id,
                    product_id=product.id,
                    quantity=existing_event_quantity,
                    sold_at=datetime(2026, 4, 15, 10, 0, 0),
                )
            )
            db.session.add(
                EventLocationTerminalSalesSummary(
                    event_location_id=event_location.id,
                    source_location=location.name,
                    total_quantity=existing_event_quantity,
                    total_amount=existing_event_quantity * float(product.price),
                    variance_details=None,
                )
            )

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id=message_id,
            attachment_filename="sales.xls",
            attachment_sha256=(message_id[-1] or "v") * 64,
            status="pending",
            sales_date=sales_date,
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name=location.name,
            normalized_location_name=location.name.lower().replace(" ", "_"),
            location_id=location.id,
            total_quantity=sales_quantity,
            net_inc=sales_quantity * float(product.price),
            computed_total=sales_quantity * float(product.price),
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        row = PosSalesImportRow(
            import_id=sales_import.id,
            location_import_id=import_location.id,
            source_product_name=product.name,
            normalized_product_name=product.name.lower().replace(" ", "_"),
            product_id=product.id,
            quantity=sales_quantity,
            net_inc=sales_quantity * float(product.price),
            computed_line_total=sales_quantity * float(product.price),
            computed_unit_price=float(product.price),
            parse_index=0,
        )
        db.session.add(row)
        db.session.commit()

        return {
            "event_id": event.id,
            "event_location_id": event_location.id,
            "import_id": sales_import.id,
            "location_id": location.id,
            "location_import_id": import_location.id,
            "row_id": row.id,
            "item_id": item.id,
            "product_id": product.id,
            "sales_date": sales_date,
        }


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
            computed_unit_price=product.price,
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


def test_admin_can_approve_sales_import_and_post_to_event_location(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    seeded = _create_event_linked_sales_import(
        app, message_id="msg-approve-event-linked"
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            f"/controlpanel/sales-imports/{seeded['import_id']}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Import approved" in response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, seeded["import_id"])
        import_location = db.session.get(
            PosSalesImportLocation, seeded["location_import_id"]
        )
        row = db.session.get(PosSalesImportRow, seeded["row_id"])
        stand_item = LocationStandItem.query.filter_by(
            location_id=seeded["location_id"],
            item_id=seeded["item_id"],
        ).one()
        item = db.session.get(Item, seeded["item_id"])
        terminal_sales = TerminalSale.query.filter_by(
            event_location_id=seeded["event_location_id"]
        ).all()
        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=seeded["event_location_id"]
        ).one()

        assert sales_import.status == "approved"
        assert import_location.event_location_id == seeded["event_location_id"]
        assert stand_item.expected_count == 25.0
        assert item.quantity == 25.0
        assert len(terminal_sales) == 1
        assert terminal_sales[0].product_id == seeded["product_id"]
        assert terminal_sales[0].quantity == 3.0
        assert terminal_sales[0].sold_at.date() == seeded["sales_date"]
        assert summary.total_quantity == 3.0
        assert summary.total_amount == 22.5

        location_metadata = json.loads(import_location.approval_metadata)
        row_metadata = json.loads(row.approval_metadata)
        assert location_metadata["mode"] == "event_location"
        assert location_metadata["event_location_id"] == seeded["event_location_id"]
        assert location_metadata["previous_state"]["terminal_sales"] == []
        assert row_metadata["target"]["mode"] == "event_location"
        assert row_metadata["target"]["event_location_id"] == seeded["event_location_id"]


def test_admin_can_undo_event_linked_sales_import_and_restore_previous_event_sales(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    seeded = _create_event_linked_sales_import(
        app,
        message_id="msg-undo-event-linked",
        existing_event_quantity=1.0,
    )

    with client:
        login(client, admin_email, admin_pass)
        approve_response = client.post(
            f"/controlpanel/sales-imports/{seeded['import_id']}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"Import approved" in approve_response.data

        undo_response = client.post(
            f"/controlpanel/sales-imports/{seeded['import_id']}",
            data={
                "action": "undo_approved_import",
                "reversal_reason": "restore prior event totals",
            },
            follow_redirects=True,
        )
        assert undo_response.status_code == 200
        assert b"Import reversal complete" in undo_response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, seeded["import_id"])
        import_location = db.session.get(
            PosSalesImportLocation, seeded["location_import_id"]
        )
        row = db.session.get(PosSalesImportRow, seeded["row_id"])
        stand_item = LocationStandItem.query.filter_by(
            location_id=seeded["location_id"],
            item_id=seeded["item_id"],
        ).one()
        item = db.session.get(Item, seeded["item_id"])
        terminal_sales = TerminalSale.query.filter_by(
            event_location_id=seeded["event_location_id"]
        ).all()
        summary = EventLocationTerminalSalesSummary.query.filter_by(
            event_location_id=seeded["event_location_id"]
        ).one()

        assert sales_import.status == "reversed"
        assert import_location.reversal_batch_id == sales_import.reversal_batch_id
        assert stand_item.expected_count == 25.0
        assert item.quantity == 25.0
        assert len(terminal_sales) == 1
        assert terminal_sales[0].quantity == 1.0
        assert summary.total_quantity == 1.0
        assert summary.total_amount == 7.5

        location_metadata = json.loads(import_location.approval_metadata)
        row_metadata = json.loads(row.approval_metadata)
        assert location_metadata["reversal"]["mode"] == "event_location"
        assert location_metadata["reversal"]["reason"] == "restore prior event totals"
        assert row_metadata["reversal"]["mode"] == "event_location"


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


def test_sales_import_detail_shows_price_review_and_blocks_unresolved_price_differences(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    review_import = _create_price_review_import(app, message_id="msg-price-review-1")

    with client:
        login(client, admin_email, admin_pass)
        detail_response = client.get(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        assert b"Price review" in detail_response.data
        assert b"File Price" in detail_response.data
        assert b"App Price" in detail_response.data
        assert b"Needs Review" in detail_response.data
        assert b"Discount Abs" not in detail_response.data
        assert b"Discount" in detail_response.data
        assert b"-1.25" in detail_response.data

        approve_response = client.post(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"price review issues" in approve_response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, review_import["import_id"])
        product = db.session.get(Product, review_import["product_id"])
        assert sales_import.status == "pending"
        assert product.price == 4.0


def test_sales_import_detail_hides_price_review_when_file_and_app_prices_align(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    review_import = _create_price_review_import(
        app,
        message_id="msg-price-review-aligned",
        product_price=5.0,
        file_price=5.0,
    )

    with client:
        login(client, admin_email, admin_pass)
        detail_response = client.get(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        assert b"Aligned" in detail_response.data
        assert b"Matching" in detail_response.data
        assert b"Price review" not in detail_response.data
        assert b"Price Used" not in detail_response.data
        assert b"Mapped Location" not in detail_response.data
        assert b"Validation Errors" not in detail_response.data


def test_sales_import_list_hides_manage_actions_for_view_only_users(client, app):
    view_import = _create_price_review_import(
        app,
        message_id="msg-view-only-list",
        product_price=5.0,
        file_price=5.0,
    )

    with app.app_context():
        user = User(
            email="sales-viewer@example.com",
            password=generate_password_hash("viewpass"),
            active=True,
            is_admin=False,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "sales_imports.view",
            group_name="Sales Import View Only",
            description="View-only access to sales imports.",
        )

    with client:
        login(client, "sales-viewer@example.com", "viewpass")
        list_response = client.get("/controlpanel/sales-imports", follow_redirects=True)
        assert list_response.status_code == 200
        assert b'class="btn btn-sm btn-success">Approve<' not in list_response.data
        assert b"Actions" not in list_response.data


def test_sales_import_detail_hides_manage_actions_for_view_only_users(
    client, app
):
    view_import = _create_unresolved_sales_import(
        app, message_id="msg-view-only-detail"
    )

    with app.app_context():
        user = User(
            email="sales-view-only-detail@example.com",
            password=generate_password_hash("detailpass"),
            active=True,
            is_admin=False,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "sales_imports.view",
            group_name="Sales Import Detail View Only",
            description="View-only access to a sales import detail page.",
        )

    with client:
        login(client, "sales-view-only-detail@example.com", "detailpass")
        detail_response = client.get(
            f"/controlpanel/sales-imports/{view_import['import_id']}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        assert b"Approve Import" not in detail_response.data
        assert b"Undo Approved Import" not in detail_response.data
        assert b"Refresh Auto-Mapping" not in detail_response.data
        assert b"Delete Import" not in detail_response.data
        assert b"Create + Map" not in detail_response.data
        assert b"Save Mapping" not in detail_response.data
        assert b"Skip Row" not in detail_response.data
        assert b"View only" in detail_response.data


def test_view_only_user_can_download_sales_import_attachment(client, app, tmp_path):
    attachment_path = tmp_path / "sales-debug.xls"
    attachment_path.write_bytes(b"debug workbook bytes")

    with app.app_context():
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-download-view-only",
            attachment_filename="sales-debug.xls",
            attachment_sha256="d" * 64,
            attachment_storage_path=str(attachment_path),
            status="pending",
        )
        db.session.add(sales_import)
        db.session.commit()
        sales_import_id = sales_import.id

        user = User(
            email="sales-download-view@example.com",
            password=generate_password_hash("downloadpass"),
            active=True,
            is_admin=False,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "sales_imports.view",
            group_name="Sales Import Download View",
            description="Can view and download staged sales import files.",
        )

    with client:
        login(client, "sales-download-view@example.com", "downloadpass")
        detail_response = client.get(
            f"/controlpanel/sales-imports/{sales_import_id}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        assert b"Download File" in detail_response.data

        download_response = client.get(
            f"/controlpanel/sales-imports/{sales_import_id}/download"
        )
        assert download_response.status_code == 200
        assert download_response.data == b"debug workbook bytes"
        assert "attachment" in download_response.headers["Content-Disposition"]
        assert "sales-debug.xls" in download_response.headers["Content-Disposition"]


def test_sales_import_detail_hides_create_and_map_without_product_create_permission(
    client, app
):
    manage_import = _create_unresolved_sales_import(
        app, message_id="msg-manage-no-product-create"
    )

    with app.app_context():
        user = User(
            email="sales-manager-no-create@example.com",
            password=generate_password_hash("managepass"),
            active=True,
            is_admin=False,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "sales_imports.manage",
            group_name="Sales Import Manager No Product Create",
            description="Can resolve import mappings but not create products.",
        )

    with client:
        login(client, "sales-manager-no-create@example.com", "managepass")
        detail_response = client.get(
            f"/controlpanel/sales-imports/{manage_import['import_id']}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200
        assert b"Approve Import" in detail_response.data
        assert b"Save Mapping" in detail_response.data
        assert b"Create + Map" not in detail_response.data
        assert b"Skip Row" in detail_response.data


def test_sales_import_detail_sidebar_shows_issue_counts_and_sorts_locations(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        clean_alpha = Location(name="Alpha Clean")
        clean_bravo = Location(name="Bravo Clean")
        issue_location = Location(name="Echo Issue")
        product_unmapped = Product(name="Mapped For Unmapped", price=3.0, cost=1.0)
        product_issue = Product(name="Issue Product", price=6.0, cost=1.0)
        product_clean_alpha = Product(name="Clean Alpha Product", price=4.0, cost=1.0)
        product_clean_bravo = Product(name="Clean Bravo Product", price=5.0, cost=1.0)
        db.session.add_all(
            [
                clean_alpha,
                clean_bravo,
                issue_location,
                product_unmapped,
                product_issue,
                product_clean_alpha,
                product_clean_bravo,
            ]
        )
        db.session.flush()

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-sidebar-issues",
            attachment_filename="sales.xls",
            attachment_sha256="1" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        unmapped_import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Zulu Unmapped",
            normalized_location_name="zulu_unmapped",
            location_id=None,
            parse_index=0,
        )
        issue_import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Echo Issue",
            normalized_location_name="echo_issue",
            location_id=issue_location.id,
            parse_index=1,
        )
        clean_alpha_import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Alpha Clean",
            normalized_location_name="alpha_clean",
            location_id=clean_alpha.id,
            parse_index=2,
        )
        clean_bravo_import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Bravo Clean",
            normalized_location_name="bravo_clean",
            location_id=clean_bravo.id,
            parse_index=3,
        )
        db.session.add_all(
            [
                unmapped_import_location,
                issue_import_location,
                clean_alpha_import_location,
                clean_bravo_import_location,
            ]
        )
        db.session.flush()

        db.session.add_all(
            [
                PosSalesImportRow(
                    import_id=sales_import.id,
                    location_import_id=unmapped_import_location.id,
                    source_product_name=product_unmapped.name,
                    normalized_product_name="mapped_for_unmapped",
                    product_id=product_unmapped.id,
                    quantity=1.0,
                    computed_unit_price=product_unmapped.price,
                    parse_index=0,
                ),
                PosSalesImportRow(
                    import_id=sales_import.id,
                    location_import_id=issue_import_location.id,
                    source_product_name=product_issue.name,
                    normalized_product_name="issue_product",
                    product_id=product_issue.id,
                    quantity=1.0,
                    computed_unit_price=7.5,
                    parse_index=0,
                ),
                PosSalesImportRow(
                    import_id=sales_import.id,
                    location_import_id=issue_import_location.id,
                    source_product_name="Still Unmapped",
                    normalized_product_name="still_unmapped",
                    product_id=None,
                    quantity=1.0,
                    parse_index=1,
                ),
                PosSalesImportRow(
                    import_id=sales_import.id,
                    location_import_id=clean_alpha_import_location.id,
                    source_product_name=product_clean_alpha.name,
                    normalized_product_name="clean_alpha_product",
                    product_id=product_clean_alpha.id,
                    quantity=1.0,
                    computed_unit_price=product_clean_alpha.price,
                    parse_index=0,
                ),
                PosSalesImportRow(
                    import_id=sales_import.id,
                    location_import_id=clean_bravo_import_location.id,
                    source_product_name=product_clean_bravo.name,
                    normalized_product_name="clean_bravo_product",
                    product_id=product_clean_bravo.id,
                    quantity=1.0,
                    computed_unit_price=product_clean_bravo.price,
                    parse_index=0,
                ),
            ]
        )
        db.session.commit()
        sales_import_id = sales_import.id

    with client:
        login(client, admin_email, admin_pass)
        detail_response = client.get(
            f"/controlpanel/sales-imports/{sales_import_id}",
            follow_redirects=True,
        )
        assert detail_response.status_code == 200

        html = detail_response.data.decode("utf-8")
        sidebar_cards = re.findall(
            r'<a\s+href="[^"]*location_id=\d+"[^>]*>.*?<div class="fw-semibold">([^<]+)</div>.*?<span class="badge ([^"]*)">\s*([^<]+)\s*</span>',
            html,
            re.S,
        )
        sidebar_names = [name.strip() for name, _, _ in sidebar_cards[:4]]
        sidebar_counts = {
            name.strip(): count.strip() for name, _, count in sidebar_cards[:4]
        }
        sidebar_badges = {
            name.strip(): classes for name, classes, _ in sidebar_cards[:4]
        }

        assert sidebar_names == [
            "Zulu Unmapped",
            "Echo Issue",
            "Alpha Clean",
            "Bravo Clean",
        ]
        assert sidebar_counts == {
            "Zulu Unmapped": "1",
            "Echo Issue": "2",
            "Alpha Clean": "0",
            "Bravo Clean": "0",
        }
        assert "bg-danger" in sidebar_badges["Zulu Unmapped"]
        assert "bg-danger" in sidebar_badges["Echo Issue"]
        assert "bg-success" in sidebar_badges["Alpha Clean"]
        assert "bg-success" in sidebar_badges["Bravo Clean"]


def test_sales_import_price_review_can_keep_app_price_on_approval(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    review_import = _create_price_review_import(app, message_id="msg-price-review-2")

    with client:
        login(client, admin_email, admin_pass)
        review_response = client.post(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            data={
                "action": "resolve_row_price",
                "selected_location_id": review_import["location_import_id"],
                "row_id": review_import["row_id"],
                "price_resolution": "app",
            },
            follow_redirects=True,
        )
        assert review_response.status_code == 200
        assert b"keep the app price" in review_response.data

        approve_response = client.post(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"Import approved" in approve_response.data

    with app.app_context():
        product = db.session.get(Product, review_import["product_id"])
        item = db.session.get(Item, review_import["item_id"])
        sales_import = db.session.get(PosSalesImport, review_import["import_id"])
        assert sales_import.status == "approved"
        assert product.price == 4.0
        assert item.quantity == 8.0


def test_sales_import_price_review_can_use_file_price_on_approval(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    review_import = _create_price_review_import(app, message_id="msg-price-review-3")

    with client:
        login(client, admin_email, admin_pass)
        review_response = client.post(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            data={
                "action": "resolve_row_price",
                "selected_location_id": review_import["location_import_id"],
                "row_id": review_import["row_id"],
                "price_resolution": "file",
            },
            follow_redirects=True,
        )
        assert review_response.status_code == 200
        assert b"use the file price" in review_response.data

        approve_response = client.post(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"Import approved" in approve_response.data

    with app.app_context():
        product = db.session.get(Product, review_import["product_id"])
        item = db.session.get(Item, review_import["item_id"])
        assert product.price == 5.0
        assert item.quantity == 8.0


def test_sales_import_price_review_can_use_custom_price_on_approval(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    review_import = _create_price_review_import(app, message_id="msg-price-review-4")

    with client:
        login(client, admin_email, admin_pass)
        review_response = client.post(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            data={
                "action": "resolve_row_price",
                "selected_location_id": review_import["location_import_id"],
                "row_id": review_import["row_id"],
                "price_resolution": "custom",
                "custom_price": "4.75",
            },
            follow_redirects=True,
        )
        assert review_response.status_code == 200
        assert b"Custom row price saved" in review_response.data

        approve_response = client.post(
            f"/controlpanel/sales-imports/{review_import['import_id']}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"Import approved" in approve_response.data

    with app.app_context():
        product = db.session.get(Product, review_import["product_id"])
        item = db.session.get(Item, review_import["item_id"])
        assert product.price == 4.75
        assert item.quantity == 8.0


def test_sales_import_row_can_be_skipped_without_product_mapping(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        location = Location(name="Skipped Import Stand")
        db.session.add(location)
        db.session.flush()

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-skip-row",
            attachment_filename="sales.xls",
            attachment_sha256="s" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name=location.name,
            normalized_location_name="skipped_import_stand",
            location_id=location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        row = PosSalesImportRow(
            import_id=sales_import.id,
            location_import_id=import_location.id,
            source_product_name="Skip Me",
            normalized_product_name="skip_me",
            product_id=None,
            quantity=2.0,
            computed_unit_price=5.0,
            parse_index=0,
        )
        db.session.add(row)
        db.session.commit()

        sales_import_id = sales_import.id
        location_import_id = import_location.id
        row_id = row.id

    with client:
        login(client, admin_email, admin_pass)
        skip_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "resolve_row_price",
                "selected_location_id": location_import_id,
                "row_id": row_id,
                "price_resolution": "skip",
            },
            follow_redirects=True,
        )
        assert skip_response.status_code == 200
        assert b"Row skipped" in skip_response.data

        approve_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"Import approved" in approve_response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        row = db.session.get(PosSalesImportRow, row_id)
        metadata = json.loads(row.approval_metadata)
        assert sales_import.status == "approved"
        assert metadata["review"]["price_action"] == "skip"


def test_sales_imports_list_shows_issue_counts_and_direct_approve_button(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    ready_import = _create_price_review_import(
        app,
        message_id="msg-list-ready",
        product_price=5.0,
        file_price=5.0,
    )
    _create_price_review_import(
        app,
        message_id="msg-list-issue",
        product_price=4.0,
        file_price=5.0,
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/controlpanel/sales-imports", follow_redirects=True)
        assert response.status_code == 200
        assert b"Issues" in response.data
        assert b"Approve" in response.data
        assert b">1<" in response.data

    with app.app_context():
        ready_record = db.session.get(PosSalesImport, ready_import["import_id"])
        assert ready_record.status == "pending"


def test_sales_imports_list_supports_search_filters_and_pagination_controls(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    ready_import = _create_price_review_import(
        app,
        message_id="msg-list-filter-ready",
        product_price=5.0,
        file_price=5.0,
    )
    approved_import = _create_price_review_import(
        app,
        message_id="msg-list-filter-approved",
        product_price=5.0,
        file_price=5.0,
    )

    with app.app_context():
        ready_record = db.session.get(PosSalesImport, ready_import["import_id"])
        approved_record = db.session.get(
            PosSalesImport, approved_import["import_id"]
        )
        ready_record.attachment_filename = "morning-pending-sales.xls"
        approved_record.attachment_filename = "evening-approved-sales.xls"
        approved_record.status = "approved"
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.get(
            "/controlpanel/sales-imports?status=approved&search=evening",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Active filters:" in response.data
        assert b"Search: evening" in response.data
        assert b"Status: Approved" in response.data
        assert b"Rows per page" in response.data
        assert b"evening-approved-sales.xls" in response.data
        assert b"morning-pending-sales.xls" not in response.data
        assert b"Already approved" in response.data
        assert b"Needs review" not in response.data


def test_sales_import_filters_can_be_saved_from_list_modal(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    _create_price_review_import(
        app,
        message_id="msg-list-save-defaults",
        product_price=5.0,
        file_price=5.0,
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/controlpanel/sales-imports", follow_redirects=True)
        assert response.status_code == 200
        body = response.get_data(as_text=True)
        token_match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"\s+disabled\s+data-filter-csrf-input',
            body,
            re.S,
        )
        assert token_match is not None

        save_response = client.post(
            "/preferences/filters",
            data={
                "scope": "admin.sales_imports",
                "status": "pending",
            },
            headers={"X-CSRFToken": token_match.group(1)},
        )
        assert save_response.status_code == 200

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).one()
        preference = UserFilterPreference.query.filter_by(
            user_id=admin_user.id,
            scope="admin.sales_imports"
        ).one()
        assert preference.values == {"status": ["pending"]}


def test_sales_imports_list_shows_ignored_status_without_review_label(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    ignored_import = _create_price_review_import(
        app,
        message_id="msg-list-ignored",
        product_price=5.0,
        file_price=5.0,
    )

    with app.app_context():
        ignored_record = db.session.get(PosSalesImport, ignored_import["import_id"])
        ignored_record.attachment_filename = "empty-ignored-sales.xls"
        ignored_record.status = PosSalesImport.STATUS_IGNORED
        ignored_record.failure_reason = (
            "Attachment does not contain any POS locations or sales rows."
        )
        for location in list(ignored_record.locations):
            db.session.delete(location)
        db.session.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.get(
            "/controlpanel/sales-imports?status=ignored",
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Status: Ignored" in response.data
        assert b"empty-ignored-sales.xls" in response.data
        assert b"Attachment does not contain any POS locations or sales rows." in response.data
        assert b"Needs review" not in response.data
        assert b"Ignored" in response.data


def test_admin_can_approve_sales_import_from_list_page(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    ready_import = _create_price_review_import(
        app,
        message_id="msg-list-approve",
        product_price=5.0,
        file_price=5.0,
    )

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/sales-imports",
            data={
                "action": "approve_import",
                "import_id": ready_import["import_id"],
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert b"Sales Imports" in response.data
        assert b"Import approved" in response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, ready_import["import_id"])
        assert sales_import.status == "approved"


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
            computed_unit_price=product.price,
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


def test_admin_can_reapprove_reversed_sales_import_after_location_override(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        original_location = Location(name="Sugar Rush")
        corrected_location = Location(name="Caesar Bar")
        item = Item(name="Override Patty", base_unit="each", quantity=40.0)
        product = Product(name="Override Burger", price=10.0, cost=3.0)
        db.session.add_all([original_location, corrected_location, item, product])
        db.session.flush()

        db.session.add(
            ProductRecipeItem(
                product_id=product.id,
                item_id=item.id,
                quantity=2.0,
                countable=True,
            )
        )
        db.session.add_all(
            [
                LocationStandItem(
                    location_id=original_location.id,
                    item_id=item.id,
                    expected_count=30.0,
                ),
                LocationStandItem(
                    location_id=corrected_location.id,
                    item_id=item.id,
                    expected_count=20.0,
                ),
            ]
        )

        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-reapprove-override-1",
            attachment_filename="sales.xls",
            attachment_sha256="r" * 64,
            status="pending",
        )
        db.session.add(sales_import)
        db.session.flush()

        import_location = PosSalesImportLocation(
            import_id=sales_import.id,
            source_location_name="Sugar Rush",
            normalized_location_name="sugar_rush",
            location_id=original_location.id,
            parse_index=0,
        )
        db.session.add(import_location)
        db.session.flush()

        db.session.add(
            PosSalesImportRow(
                import_id=sales_import.id,
                location_import_id=import_location.id,
                source_product_name="Override Burger",
                normalized_product_name="override_burger",
                product_id=product.id,
                quantity=4.0,
                computed_unit_price=product.price,
                parse_index=0,
            )
        )
        db.session.commit()
        sales_import_id = sales_import.id
        location_import_id = import_location.id
        original_location_id = original_location.id
        corrected_location_id = corrected_location.id
        item_id = item.id

    with client:
        login(client, admin_email, admin_pass)

        approve_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={"action": "approve_import"},
            follow_redirects=True,
        )
        assert approve_response.status_code == 200
        assert b"Import approved" in approve_response.data

        with app.app_context():
            first_approval_batch_id = (
                db.session.get(PosSalesImport, sales_import_id).approval_batch_id
            )

        undo_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "undo_approved_import",
                "reversal_reason": "Need to move sales to Caesar Bar",
            },
            follow_redirects=True,
        )
        assert undo_response.status_code == 200
        assert b"can be approved again" in undo_response.data

        remap_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "map_location",
                "location_import_id": location_import_id,
                "target_location_id": corrected_location_id,
                "selected_location_id": location_import_id,
            },
            follow_redirects=True,
        )
        assert remap_response.status_code == 200
        assert b"Location mapping saved" in remap_response.data

        reapprove_response = client.post(
            f"/controlpanel/sales-imports/{sales_import_id}",
            data={
                "action": "approve_import",
                "selected_location_id": location_import_id,
            },
            follow_redirects=True,
        )
        assert reapprove_response.status_code == 200
        assert b"Import approved" in reapprove_response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        import_location = db.session.get(PosSalesImportLocation, location_import_id)
        original_record = LocationStandItem.query.filter_by(
            location_id=original_location_id,
            item_id=item_id,
        ).one()
        corrected_record = LocationStandItem.query.filter_by(
            location_id=corrected_location_id,
            item_id=item_id,
        ).one()
        refreshed_item = db.session.get(Item, item_id)

        assert sales_import.status == "approved"
        assert sales_import.approval_batch_id != first_approval_batch_id
        assert import_location.location_id == corrected_location_id
        assert original_record.expected_count == 30.0
        assert corrected_record.expected_count == 12.0
        assert refreshed_item.quantity == 32.0


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


def test_admin_can_soft_delete_sales_import(client, app, tmp_path):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    attachment_path = tmp_path / "sales-delete-unique.xls"
    attachment_path.write_bytes(b"delete me")

    with app.app_context():
        sales_import = PosSalesImport(
            source_provider="mailgun",
            message_id="msg-delete-1",
            attachment_filename="sales-delete-unique.xls",
            attachment_sha256="f" * 64,
            attachment_storage_path=str(attachment_path),
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
        assert b"Sales Imports" in response.data
        assert b"sales-delete-unique.xls" not in response.data

    with app.app_context():
        sales_import = db.session.get(PosSalesImport, sales_import_id)
        assert sales_import is not None
        assert sales_import.status == "deleted"
        assert sales_import.attachment_storage_path is None
        assert sales_import.deleted_by is not None
        assert sales_import.deleted_at is not None
        assert sales_import.deletion_reason == "Duplicate staging run"

    assert not attachment_path.exists()

    with client:
        login(client, admin_email, admin_pass)
        response = client.get("/controlpanel/sales-imports", follow_redirects=True)
        assert response.status_code == 200
        assert b"sales-delete-unique.xls" not in response.data


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
                computed_unit_price=product.price,
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
                computed_unit_price=product.price,
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
