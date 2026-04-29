from __future__ import annotations

from io import BytesIO
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from werkzeug.security import generate_password_hash

from app import db
from app.models import (
    ActivityLog,
    EquipmentAsset,
    EquipmentCategory,
    EquipmentCustodyEvent,
    EquipmentIntakeBatch,
    EquipmentMaintenanceIssue,
    EquipmentMaintenanceUpdate,
    EquipmentModel,
    Location,
    Note,
    PurchaseInvoice,
    PurchaseOrder,
    User,
    Vendor,
)
from app.services.equipment_labels import render_equipment_label_pdf
from app.utils.activity import flush_activity_logs
from tests.permission_helpers import grant_permissions
from tests.utils import login


def _create_user(app, email: str, *permissions: str) -> str:
    with app.app_context():
        user = User(
            email=email,
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        if permissions:
            grant_permissions(
                user,
                *permissions,
                group_name=f"Equipment Test Group {email}",
                description="Equipment feature test permissions.",
            )
        return user.email


def _seed_equipment_dependencies(app, *, suffix: str = "") -> dict[str, int]:
    with app.app_context():
        purchase_vendor = Vendor(
            first_name=f"Purchase{suffix}",
            last_name="Vendor",
        )
        service_vendor = Vendor(
            first_name=f"Service{suffix}",
            last_name="Vendor",
        )
        location = Location(name=f"Equipment Location {suffix or 'A'}")
        custodian = User(
            email=f"custodian{suffix or 'a'}@example.com",
            password=generate_password_hash("pass"),
            active=True,
            display_name=f"Custodian {suffix or 'A'}",
        )
        db.session.add_all([purchase_vendor, service_vendor, location, custodian])
        db.session.commit()
        return {
            "purchase_vendor_id": purchase_vendor.id,
            "service_vendor_id": service_vendor.id,
            "location_id": location.id,
            "custodian_id": custodian.id,
        }


def _create_equipment_model(app, *, suffix: str) -> int:
    with app.app_context():
        category = EquipmentCategory(name=f"Category {suffix}")
        equipment_model = EquipmentModel(
            category=category,
            manufacturer=f"Maker {suffix}",
            name=f"Model {suffix}",
            model_number=f"MN-{suffix}",
        )
        db.session.add_all([category, equipment_model])
        db.session.commit()
        return equipment_model.id


def _create_equipment_asset(
    app,
    *,
    asset_tag: str,
    status: str = EquipmentAsset.STATUS_OPERATIONAL,
    suffix: str = "",
    **asset_kwargs,
) -> int:
    with app.app_context():
        category = EquipmentCategory(name=f"Category {asset_tag}")
        equipment_model = EquipmentModel(
            category=category,
            manufacturer=f"Maker {suffix or asset_tag}",
            name=f"Model {asset_tag}",
            model_number=f"MN-{asset_tag}",
        )
        location = Location(name=f"Location {asset_tag}")
        custodian = User(
            email=f"user-{asset_tag.lower()}@example.com",
            password=generate_password_hash("pass"),
            active=True,
            display_name=f"Holder {asset_tag}",
        )
        purchase_vendor = Vendor(first_name=f"Purchase {asset_tag}", last_name="Vendor")
        db.session.add_all(
            [category, equipment_model, location, custodian, purchase_vendor]
        )
        db.session.flush()
        home_location_id = asset_kwargs.pop("home_location_id", location.id)
        asset = EquipmentAsset(
            equipment_model=equipment_model,
            asset_tag=asset_tag,
            name=f"Asset {asset_tag}",
            serial_number=f"SER-{asset_tag}",
            status=status,
            purchase_vendor_id=purchase_vendor.id,
            location_id=location.id,
            home_location_id=home_location_id,
            assigned_user_id=custodian.id,
            **asset_kwargs,
        )
        db.session.add(asset)
        db.session.commit()
        return asset.id


def _create_maintenance_issue(
    app,
    *,
    asset_id: int,
    title: str,
    status: str = EquipmentMaintenanceIssue.STATUS_OPEN,
    priority: str = EquipmentMaintenanceIssue.PRIORITY_MEDIUM,
    due_on=None,
) -> int:
    with app.app_context():
        issue = EquipmentMaintenanceIssue(
            equipment_asset_id=asset_id,
            title=title,
            status=status,
            priority=priority,
            reported_on=date.today(),
            due_on=due_on,
        )
        db.session.add(issue)
        db.session.commit()
        return issue.id


def test_equipment_crud_and_notes_flow(client, app):
    manager_email = _create_user(
        app,
        "equipment-manager@example.com",
        "equipment.view",
        "equipment.create",
        "equipment.edit",
        "equipment.archive",
        "equipment.manage_categories",
        "equipment.manage_models",
        "equipment.print_labels",
    )
    deps = _seed_equipment_dependencies(app, suffix="Crud")

    with client:
        login(client, manager_email, "pass")

        create_category = client.post(
            "/equipment/categories/create",
            data={
                "name": "Refrigeration",
                "description": "Cooling and freezer assets.",
            },
            follow_redirects=True,
        )
        assert create_category.status_code == 200
        assert b"Refrigeration" in create_category.data

        with app.app_context():
            category = EquipmentCategory.query.filter_by(
                name="Refrigeration"
            ).one()

        create_model = client.post(
            "/equipment/models/create",
            data={
                "category_id": str(category.id),
                "manufacturer": "True",
                "name": "T-49F",
                "model_number": "T49F-001",
                "description": "Two door freezer",
            },
            follow_redirects=True,
        )
        assert create_model.status_code == 200
        assert b"T-49F" in create_model.data

        with app.app_context():
            equipment_model = EquipmentModel.query.filter_by(name="T-49F").one()

        create_asset = client.post(
            "/equipment/create",
            data={
                "equipment_model_id": str(equipment_model.id),
                "name": "Walk-in Freezer Controller",
                "asset_tag": "FRZ-001",
                "serial_number": "SER-FRZ-001",
                "status": EquipmentAsset.STATUS_OPERATIONAL,
                "description": "Primary controller",
                "acquired_on": "2026-01-15",
                "warranty_expires_on": "2027-01-15",
                "cost": "1999.95",
                "purchase_vendor_id": str(deps["purchase_vendor_id"]),
                "service_vendor_id": str(deps["service_vendor_id"]),
                "service_contact_name": "Service Desk",
                "service_contact_email": "service@example.com",
                "service_contact_phone": "555-0100",
                "location_id": str(deps["location_id"]),
                "home_location_id": str(deps["location_id"]),
                "assigned_user_id": str(deps["custodian_id"]),
                "label_qr_target": EquipmentAsset.QR_TARGET_SCAN,
                "label_qr_custom_url": "",
            },
            follow_redirects=True,
        )
        assert create_asset.status_code == 200
        assert b"FRZ-001" in create_asset.data
        assert b"Walk-in Freezer Controller" in create_asset.data

        with app.app_context():
            asset = EquipmentAsset.query.filter_by(asset_tag="FRZ-001").one()
            asset_id = asset.id

        add_note = client.post(
            f"/notes/equipment/{asset_id}",
            data={"content": "Installed in the back hallway."},
            follow_redirects=True,
        )
        assert add_note.status_code == 200
        assert b"Installed in the back hallway." in add_note.data

        edit_asset = client.post(
            f"/equipment/{asset_id}/edit",
            data={
                "equipment_model_id": str(equipment_model.id),
                "name": "Walk-in Freezer Controller V2",
                "asset_tag": "FRZ-001",
                "serial_number": "SER-FRZ-001",
                "status": EquipmentAsset.STATUS_NEEDS_SERVICE,
                "description": "Needs a service inspection.",
                "acquired_on": "2026-01-15",
                "warranty_expires_on": "2027-01-15",
                "cost": "2099.95",
                "purchase_vendor_id": str(deps["purchase_vendor_id"]),
                "service_vendor_id": str(deps["service_vendor_id"]),
                "service_contact_name": "Service Desk",
                "service_contact_email": "service@example.com",
                "service_contact_phone": "555-0100",
                "location_id": str(deps["location_id"]),
                "home_location_id": str(deps["location_id"]),
                "assigned_user_id": str(deps["custodian_id"]),
                "label_qr_target": EquipmentAsset.QR_TARGET_CUSTOM,
                "label_qr_custom_url": "https://example.com/custom-eq/FRZ-001",
            },
            follow_redirects=True,
        )
        assert edit_asset.status_code == 200
        assert b"Needs Service" in edit_asset.data
        assert b"Walk-in Freezer Controller V2" in edit_asset.data

        archive_asset = client.post(
            f"/equipment/{asset_id}/archive",
            follow_redirects=True,
        )
        assert archive_asset.status_code == 200

    with app.app_context():
        asset = EquipmentAsset.query.filter_by(asset_tag="FRZ-001").one()
        assert asset.archived is True
        assert asset.status == EquipmentAsset.STATUS_NEEDS_SERVICE
        assert asset.home_location_id == deps["location_id"]
        assert asset.label_qr_target == EquipmentAsset.QR_TARGET_CUSTOM
        assert asset.label_qr_custom_url == "https://example.com/custom-eq/FRZ-001"
        assert Note.query.filter_by(
            entity_type="equipment", entity_id=str(asset.id)
        ).count() == 1

        flush_activity_logs()
        activities = [entry.activity for entry in ActivityLog.query.all()]
        assert any("Created equipment category Refrigeration" in entry for entry in activities)
        assert any("Created equipment model True T-49F T49F-001" in entry for entry in activities)
        assert any("Created equipment FRZ-001" in entry for entry in activities)
        assert any("Edited equipment FRZ-001" in entry for entry in activities)
        assert any("Archived equipment FRZ-001" in entry for entry in activities)
        assert any("Added note to equipment FRZ-001" in entry for entry in activities)


def test_equipment_list_filters_and_defaults(client, app, save_filter_defaults):
    viewer_email = _create_user(app, "equipment-viewer@example.com", "equipment.view")

    with app.app_context():
        category = EquipmentCategory(name="POS")
        model = EquipmentModel(
            category=category,
            manufacturer="Epson",
            name="TM-T88VII",
            model_number="POS-1",
        )
        location = Location(name="Front Counter")
        user = User(
            email="front-counter@example.com",
            password=generate_password_hash("pass"),
            active=True,
            display_name="Front Counter Holder",
        )
        db.session.add_all([category, model, location, user])
        db.session.flush()
        db.session.add_all(
            [
                EquipmentAsset(
                    equipment_model=model,
                    asset_tag="POS-001",
                    name="Front POS Printer",
                    status=EquipmentAsset.STATUS_OPERATIONAL,
                    location_id=location.id,
                    home_location_id=location.id,
                    assigned_user_id=user.id,
                    checked_out_at=datetime.utcnow(),
                ),
                EquipmentAsset(
                    equipment_model=model,
                    asset_tag="POS-002",
                    name="Spare POS Printer",
                    status=EquipmentAsset.STATUS_NEEDS_SERVICE,
                ),
            ]
        )
        db.session.commit()

    with client:
        login(client, viewer_email, "pass")
        filtered = client.get("/equipment?status=needs_service")
        assert filtered.status_code == 200
        assert b"POS-002" in filtered.data
        assert b"POS-001" not in filtered.data

        searched = client.get("/equipment?search_query=Front")
        assert searched.status_code == 200
        assert b"Front POS Printer" in searched.data
        assert b"Spare POS Printer" not in searched.data

        checked_out = client.get("/equipment?custody_state=checked_out")
        assert checked_out.status_code == 200
        assert b"POS-001" in checked_out.data
        assert b"POS-002" not in checked_out.data

        save_filter_defaults(
            "equipment.view_equipment",
            {"status": ["needs_service"]},
            token_path="/equipment",
        )
        redirected = client.get("/equipment", follow_redirects=False)
        assert redirected.status_code == 302
        assert "status=needs_service" in redirected.headers["Location"]


def test_equipment_permissions_hide_management_ui_and_protect_notes(client, app):
    viewer_email = _create_user(app, "equipment-ui-viewer@example.com", "equipment.view")
    unprivileged_email = _create_user(app, "equipment-ui-unprivileged@example.com")
    asset_id = _create_equipment_asset(app, asset_tag="UI-001")

    with client:
        login(client, viewer_email, "pass")
        equipment_page = client.get("/equipment")
        assert equipment_page.status_code == 200
        html = equipment_page.get_data(as_text=True)
        assert "Add Equipment" not in html
        assert "Manage Catalog" not in html
        assert "Print Selected Labels" not in html
        assert "Scan" not in html
        assert f"/equipment/{asset_id}/edit" not in html
        assert f"/equipment/{asset_id}/archive" not in html
        assert f"/equipment/labels/print?equipment_id={asset_id}" not in html
        assert "Notes" in html

        detail_page = client.get(f"/equipment/{asset_id}")
        assert detail_page.status_code == 200
        detail_html = detail_page.get_data(as_text=True)
        assert "Print Label" not in detail_html
        assert "Scan Page" not in detail_html
        assert "Sign In" not in detail_html
        assert "Sign Out" not in detail_html
        assert ">Edit<" not in detail_html
        assert ">Archive<" not in detail_html
        assert "Notes" in detail_html

        notes_page = client.get(f"/notes/equipment/{asset_id}")
        assert notes_page.status_code == 200

        login(client, unprivileged_email, "pass")
        assert client.get("/equipment").status_code == 403
        assert client.get(f"/notes/equipment/{asset_id}").status_code == 403
        assert client.get(f"/equipment/{asset_id}/scan").status_code == 403


def test_equipment_maintenance_workflow_and_history(client, app):
    manager_email = _create_user(
        app,
        "equipment-maintenance-manager@example.com",
        "equipment.view",
        "equipment.manage_maintenance",
    )
    asset_id = _create_equipment_asset(app, asset_tag="MNT-001")

    with client:
        login(client, manager_email, "pass")

        create_issue = client.post(
            "/equipment/maintenance/create",
            data={
                "equipment_asset_id": str(asset_id),
                "title": "Cooling fan failure",
                "description": "The internal fan stops after 10 minutes.",
                "priority": EquipmentMaintenanceIssue.PRIORITY_HIGH,
                "status": EquipmentMaintenanceIssue.STATUS_OPEN,
                "reported_on": "2026-04-10",
                "due_on": "2026-04-12",
                "assigned_user_id": "0",
                "assigned_vendor_id": "0",
                "parts_cost": "45.50",
                "labor_cost": "20.00",
                "downtime_started_on": "2026-04-10",
                "resolved_on": "",
                "resolution_summary": "",
            },
            follow_redirects=True,
        )
        assert create_issue.status_code == 200
        assert b"Cooling fan failure" in create_issue.data

        with app.app_context():
            issue = EquipmentMaintenanceIssue.query.filter_by(
                title="Cooling fan failure"
            ).one()
            issue_id = issue.id

        resolved_update = client.post(
            f"/equipment/maintenance/{issue_id}/updates",
            data={
                "message": "Replaced the failed fan assembly.",
                "status": EquipmentMaintenanceIssue.STATUS_RESOLVED,
            },
            follow_redirects=True,
        )
        assert resolved_update.status_code == 200
        assert b"Replaced the failed fan assembly." in resolved_update.data
        assert b"Resolved" in resolved_update.data

        reopened_update = client.post(
            f"/equipment/maintenance/{issue_id}/updates",
            data={
                "message": "The replacement failed after another test cycle.",
                "status": EquipmentMaintenanceIssue.STATUS_OPEN,
            },
            follow_redirects=True,
        )
        assert reopened_update.status_code == 200
        assert b"The replacement failed after another test cycle." in reopened_update.data
        assert b"Open" in reopened_update.data

        issue_page = client.get(f"/equipment/maintenance/{issue_id}")
        assert issue_page.status_code == 200
        issue_html = issue_page.get_data(as_text=True)
        assert "Maintenance History" in issue_html
        assert "Cooling fan failure" in issue_html

    with app.app_context():
        issue = db.session.get(EquipmentMaintenanceIssue, issue_id)
        assert issue.status == EquipmentMaintenanceIssue.STATUS_OPEN
        assert issue.reopened_count == 1
        assert issue.total_cost == 65.5
        assert (
            EquipmentMaintenanceUpdate.query.filter_by(issue_id=issue_id).count()
            == 3
        )

        flush_activity_logs()
        activities = [entry.activity for entry in ActivityLog.query.all()]
        assert any(
            f"Created maintenance issue #{issue_id} for equipment MNT-001" in activity
            for activity in activities
        )
        assert any(
            f"Updated maintenance issue #{issue_id} for equipment MNT-001 to resolved"
            in activity
            for activity in activities
        )
        assert any(
            f"Updated maintenance issue #{issue_id} for equipment MNT-001 to open"
            in activity
            for activity in activities
        )


def test_equipment_maintenance_permissions_hide_ui_and_protect_routes(client, app):
    viewer_email = _create_user(app, "equipment-maint-viewer@example.com", "equipment.view")
    unprivileged_email = _create_user(app, "equipment-maint-unprivileged@example.com")
    asset_id = _create_equipment_asset(app, asset_tag="MNT-UI-001")
    issue_id = _create_maintenance_issue(
        app,
        asset_id=asset_id,
        title="Door gasket tear",
    )

    with client:
        login(client, viewer_email, "pass")
        maintenance_page = client.get("/equipment/maintenance")
        assert maintenance_page.status_code == 200
        maintenance_html = maintenance_page.get_data(as_text=True)
        assert "Report Issue" not in maintenance_html
        assert f"/equipment/maintenance/{issue_id}/edit" not in maintenance_html

        detail_page = client.get(f"/equipment/maintenance/{issue_id}")
        assert detail_page.status_code == 200
        detail_html = detail_page.get_data(as_text=True)
        assert "Add Update" not in detail_html
        assert "Edit Issue" not in detail_html

        asset_page = client.get(f"/equipment/{asset_id}")
        assert asset_page.status_code == 200
        asset_html = asset_page.get_data(as_text=True)
        assert "Maintenance Queue" in asset_html
        assert "Report Issue" not in asset_html

        assert client.get("/equipment/maintenance/create").status_code == 403
        assert client.get(f"/equipment/maintenance/{issue_id}/edit").status_code == 403
        assert (
            client.post(
                f"/equipment/maintenance/{issue_id}/updates",
                data={"message": "Attempted unauthorized update."},
            ).status_code
            == 403
        )

        login(client, unprivileged_email, "pass")
        assert client.get("/equipment/maintenance").status_code == 403
        assert client.get(f"/equipment/maintenance/{issue_id}").status_code == 403


def test_equipment_attention_filters_and_maintenance_defaults(
    client, app, save_filter_defaults
):
    viewer_email = _create_user(app, "equipment-attention-viewer@example.com", "equipment.view")
    due_asset_id = _create_equipment_asset(
        app,
        asset_tag="ATTN-001",
        next_service_due_on=date.today() + timedelta(days=3),
        warranty_expires_on=date.today() + timedelta(days=4),
    )
    clear_asset_id = _create_equipment_asset(
        app,
        asset_tag="ATTN-002",
        next_service_due_on=date.today() + timedelta(days=90),
        warranty_expires_on=date.today() + timedelta(days=120),
    )
    overdue_issue_id = _create_maintenance_issue(
        app,
        asset_id=due_asset_id,
        title="Thermostat calibration",
        due_on=date.today() - timedelta(days=1),
    )
    _create_maintenance_issue(
        app,
        asset_id=clear_asset_id,
        title="Completed service visit",
        status=EquipmentMaintenanceIssue.STATUS_RESOLVED,
        due_on=date.today(),
    )

    with client:
        login(client, viewer_email, "pass")

        attention_page = client.get("/equipment?attention_state=needs_attention")
        assert attention_page.status_code == 200
        assert b"ATTN-001" in attention_page.data
        assert b"Asset ATTN-002" not in attention_page.data

        overdue_page = client.get("/equipment/maintenance?due_state=overdue")
        assert overdue_page.status_code == 200
        assert b"Thermostat calibration" in overdue_page.data
        assert b"Completed service visit" not in overdue_page.data

        issue_detail = client.get(f"/equipment/maintenance/{overdue_issue_id}")
        assert issue_detail.status_code == 200

        save_filter_defaults(
            "equipment.view_equipment_maintenance",
            {"priority": ["critical"]},
            token_path="/equipment/maintenance",
        )
        redirected = client.get("/equipment/maintenance", follow_redirects=False)
        assert redirected.status_code == 302
        assert "priority=critical" in redirected.headers["Location"]


def test_equipment_intake_receive_flow_and_notes(client, app):
    manager_email = _create_user(
        app,
        "equipment-intake-manager@example.com",
        "equipment.view",
        "equipment.manage_intake",
    )
    deps = _seed_equipment_dependencies(app, suffix="Intake")
    model_id = _create_equipment_model(app, suffix="Intake")

    with client:
        login(client, manager_email, "pass")
        create_batch = client.post(
            "/equipment/intake/create",
            data={
                "equipment_model_id": str(model_id),
                "source_type": EquipmentIntakeBatch.SOURCE_MANUAL,
                "expected_quantity": "2",
                "unit_cost": "349.99",
                "purchase_vendor_id": str(deps["purchase_vendor_id"]),
                "vendor_name": "Kitchen Supply Co",
                "purchase_order_reference": "PO-1001",
                "purchase_invoice_reference": "INV-1001",
                "order_date": "2026-04-10",
                "expected_received_on": "2026-04-15",
                "received_on": "",
                "location_id": str(deps["location_id"]),
                "assigned_user_id": str(deps["custodian_id"]),
                "notes": "Receiving a pair of prep-line coolers.",
            },
            follow_redirects=True,
        )
        assert create_batch.status_code == 200
        assert b"Intake Batch" in create_batch.data
        assert b"PO-1001" in create_batch.data

        with app.app_context():
            batch = EquipmentIntakeBatch.query.filter_by(
                purchase_order_reference="PO-1001"
            ).one()
            batch_id = batch.id

        add_note = client.post(
            f"/notes/equipment_intake/{batch_id}",
            data={"content": "Arrived at the loading dock."},
            follow_redirects=True,
        )
        assert add_note.status_code == 200
        assert b"Arrived at the loading dock." in add_note.data

        receive_assets = client.post(
            f"/equipment/intake/{batch_id}/receive",
            data={
                "quantity": "2",
                "status": EquipmentAsset.STATUS_OPERATIONAL,
                "acquired_on": "2026-04-16",
                "warranty_expires_on": "2027-04-16",
                "cost": "349.99",
                "location_id": str(deps["location_id"]),
                "assigned_user_id": str(deps["custodian_id"]),
                "asset_rows": (
                    "INT-001,SER-INT-001,Prep Cooler Left\n"
                    "INT-002,SER-INT-002,Prep Cooler Right"
                ),
            },
            follow_redirects=True,
        )
        assert receive_assets.status_code == 200
        assert b"INT-001" in receive_assets.data
        assert b"Prep Cooler Left" in receive_assets.data

        with app.app_context():
            received_asset_id = EquipmentAsset.query.filter_by(asset_tag="INT-001").one().id

        asset_detail = client.get(f"/equipment/{received_asset_id}")
        assert asset_detail.status_code == 200

    with app.app_context():
        batch = db.session.get(EquipmentIntakeBatch, batch_id)
        assert batch.status == EquipmentIntakeBatch.STATUS_RECEIVED
        assert batch.received_asset_count == 2
        assert batch.remaining_quantity == 0

        received_assets = (
            EquipmentAsset.query.filter(
                EquipmentAsset.asset_tag.in_(["INT-001", "INT-002"])
            )
            .order_by(EquipmentAsset.asset_tag.asc())
            .all()
        )
        assert len(received_assets) == 2
        assert all(asset.equipment_intake_batch_id == batch_id for asset in received_assets)
        assert received_assets[0].location_id == deps["location_id"]
        assert received_assets[0].home_location_id == deps["location_id"]
        assert received_assets[0].assigned_user_id == deps["custodian_id"]

        flush_activity_logs()
        activities = [entry.activity for entry in ActivityLog.query.all()]
        assert any(
            f"Created equipment intake batch #{batch_id}" in activity
            for activity in activities
        )
        assert any(
            f"Received 2 equipment asset(s) into intake batch #{batch_id}" in activity
            for activity in activities
        )
        assert any(
            "Added note to equipment intake batch" in activity
            for activity in activities
        )


def test_equipment_intake_permissions_hide_ui_and_protect_routes(client, app):
    viewer_email = _create_user(
        app,
        "equipment-intake-viewer@example.com",
        "equipment.view",
        "purchase_orders.view",
        "purchase_orders.edit",
        "purchase_invoices.view",
    )
    outsider_email = _create_user(app, "equipment-intake-outsider@example.com")
    deps = _seed_equipment_dependencies(app, suffix="Perms")
    model_id = _create_equipment_model(app, suffix="Perms")

    with app.app_context():
        vendor = db.session.get(Vendor, deps["purchase_vendor_id"])
        purchase_order = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=deps["custodian_id"],
            vendor_name=f"{vendor.first_name} {vendor.last_name}".strip(),
            order_number="PO-PERMS-1",
            order_date=date(2026, 4, 7),
            expected_date=date(2026, 4, 10),
            status=PurchaseOrder.STATUS_ORDERED,
            received=False,
        )
        db.session.add(purchase_order)
        db.session.flush()
        purchase_invoice = PurchaseInvoice(
            purchase_order_id=purchase_order.id,
            user_id=deps["custodian_id"],
            location_id=deps["location_id"],
            vendor_name=purchase_order.vendor_name,
            location_name=db.session.get(Location, deps["location_id"]).name,
            received_date=date(2026, 4, 11),
            invoice_number="INV-PERMS-1",
            gst=0.0,
            pst=0.0,
            delivery_charge=0.0,
        )
        db.session.add(purchase_invoice)
        db.session.flush()
        batch = EquipmentIntakeBatch(
            equipment_model_id=model_id,
            purchase_vendor_id=deps["purchase_vendor_id"],
            vendor_name="Perms Vendor",
            purchase_order_reference="PO-PERMS",
            source_type=EquipmentIntakeBatch.SOURCE_MANUAL,
            status=EquipmentIntakeBatch.STATUS_OPEN,
            expected_quantity=1,
            location_id=deps["location_id"],
            assigned_user_id=deps["custodian_id"],
        )
        db.session.add(batch)
        db.session.commit()
        purchase_order_id = purchase_order.id
        purchase_invoice_id = purchase_invoice.id
        batch_id = batch.id

    with client:
        login(client, viewer_email, "pass")
        purchase_orders_page = client.get("/purchase_orders")
        assert purchase_orders_page.status_code == 200
        purchase_orders_html = purchase_orders_page.get_data(as_text=True)
        assert (
            f"/equipment/intake/create?purchase_order_id={purchase_order_id}"
            not in purchase_orders_html
        )

        purchase_order_edit_page = client.get(
            f"/purchase_orders/edit/{purchase_order_id}"
        )
        assert purchase_order_edit_page.status_code == 200
        assert "Create Equipment Intake" not in purchase_order_edit_page.get_data(
            as_text=True
        )

        purchase_invoice_page = client.get(f"/purchase_invoices/{purchase_invoice_id}")
        assert purchase_invoice_page.status_code == 200
        assert "Create Equipment Intake" not in purchase_invoice_page.get_data(
            as_text=True
        )

        intake_page = client.get("/equipment/intake")
        assert intake_page.status_code == 200
        intake_html = intake_page.get_data(as_text=True)
        assert "Create Intake Batch" not in intake_html
        assert "Import Snipe-IT CSV" not in intake_html
        assert f"/equipment/intake/{batch_id}/receive" not in intake_html
        assert f"/equipment/intake/{batch_id}/edit" not in intake_html

        detail_page = client.get(f"/equipment/intake/{batch_id}")
        assert detail_page.status_code == 200
        detail_html = detail_page.get_data(as_text=True)
        assert "Receive Assets" not in detail_html
        assert ">Edit<" not in detail_html

        notes_page = client.get(f"/notes/equipment_intake/{batch_id}")
        assert notes_page.status_code == 200

        assert client.get("/equipment/intake/create").status_code == 403
        assert client.get(f"/equipment/intake/{batch_id}/edit").status_code == 403
        assert client.get(f"/equipment/intake/{batch_id}/receive").status_code == 403
        assert client.get("/equipment/import/snipe-it").status_code == 403
        assert client.get("/reports/equipment-procurement").status_code == 403

        login(client, outsider_email, "pass")
        assert client.get("/equipment/intake").status_code == 403
        assert client.get(f"/notes/equipment_intake/{batch_id}").status_code == 403


def test_equipment_procurement_report_and_invoice_prefill(client, app):
    manager_email = _create_user(
        app,
        "equipment-procurement@example.com",
        "equipment.view",
        "equipment.manage_intake",
        "reports.equipment_procurement",
        "purchase_orders.view",
        "purchase_orders.edit",
        "purchase_invoices.view",
    )
    deps = _seed_equipment_dependencies(app, suffix="Report")
    model_id = _create_equipment_model(app, suffix="Report")

    with app.app_context():
        manager = User.query.filter_by(email=manager_email).one()
        vendor = db.session.get(Vendor, deps["purchase_vendor_id"])
        location = db.session.get(Location, deps["location_id"])
        purchase_order = PurchaseOrder(
            vendor_id=vendor.id,
            user_id=manager.id,
            vendor_name=f"{vendor.first_name} {vendor.last_name}".strip(),
            order_number="PO-REPORT-1",
            order_date=date(2026, 4, 1),
            expected_date=date(2026, 4, 5),
            status=PurchaseOrder.STATUS_ORDERED,
            received=False,
        )
        db.session.add(purchase_order)
        db.session.flush()
        purchase_invoice = PurchaseInvoice(
            purchase_order_id=purchase_order.id,
            user_id=manager.id,
            location_id=location.id,
            vendor_name=purchase_order.vendor_name,
            location_name=location.name,
            received_date=date(2026, 4, 6),
            invoice_number="INV-REPORT-1",
            gst=0.0,
            pst=0.0,
            delivery_charge=0.0,
        )
        db.session.add(purchase_invoice)
        db.session.flush()
        batch = EquipmentIntakeBatch(
            equipment_model_id=model_id,
            purchase_vendor_id=vendor.id,
            vendor_name=purchase_order.vendor_name,
            purchase_order_id=purchase_order.id,
            purchase_invoice_id=purchase_invoice.id,
            purchase_order_reference=purchase_order.order_number,
            purchase_invoice_reference=purchase_invoice.invoice_number,
            source_type=EquipmentIntakeBatch.SOURCE_PURCHASE_INVOICE,
            status=EquipmentIntakeBatch.STATUS_RECEIVED,
            expected_quantity=1,
            unit_cost=899.99,
            order_date=purchase_order.order_date,
            expected_received_on=purchase_order.expected_date,
            received_on=purchase_invoice.received_date,
            location_id=location.id,
            assigned_user_id=deps["custodian_id"],
            created_by_id=manager.id,
        )
        db.session.add(batch)
        db.session.flush()
        db.session.add(
            EquipmentAsset(
                equipment_model_id=model_id,
                equipment_intake_batch_id=batch.id,
                asset_tag="RPT-001",
                serial_number="SER-RPT-001",
                status=EquipmentAsset.STATUS_OPERATIONAL,
                cost=899.99,
            )
        )
        db.session.commit()
        batch_id = batch.id
        purchase_order_id = purchase_order.id
        invoice_id = purchase_invoice.id

    with client:
        login(client, manager_email, "pass")
        purchase_orders_page = client.get("/purchase_orders")
        assert purchase_orders_page.status_code == 200
        purchase_orders_html = purchase_orders_page.get_data(as_text=True)
        assert (
            f"/equipment/intake/create?purchase_order_id={purchase_order_id}"
            not in purchase_orders_html
        )

        purchase_order_edit_page = client.get(
            f"/purchase_orders/edit/{purchase_order_id}"
        )
        assert purchase_order_edit_page.status_code == 200
        assert "Create Equipment Intake" not in purchase_order_edit_page.get_data(
            as_text=True
        )

        po_prefill_page = client.get(
            f"/equipment/intake/create?purchase_order_id={purchase_order_id}"
        )
        assert po_prefill_page.status_code == 200
        assert b"PO-REPORT-1" in po_prefill_page.data

        invoice_page = client.get(f"/purchase_invoices/{invoice_id}")
        assert invoice_page.status_code == 200
        assert b"Create Equipment Intake" in invoice_page.data

        prefill_page = client.get(f"/equipment/intake/create?purchase_invoice_id={invoice_id}")
        assert prefill_page.status_code == 200
        assert b"INV-REPORT-1" in prefill_page.data
        assert b"PO-REPORT-1" in prefill_page.data

        report_page = client.post(
            "/reports/equipment-procurement",
            data={
                "start_date": "2026-04-01",
                "end_date": "2026-04-30",
                "category_id": "0",
                "equipment_model_id": "0",
                "purchase_vendor_id": str(deps["purchase_vendor_id"]),
                "location_id": "0",
                "source_type": "all",
                "status": "all",
            },
        )
        assert report_page.status_code == 200
        assert b"Equipment Procurement Report" in report_page.data
        assert f"#{batch_id}".encode() in report_page.data
        assert b"INV-REPORT-1" in report_page.data


def test_snipe_it_import_creates_batches_assets_and_updates_existing(client, app):
    importer_email = _create_user(
        app,
        "equipment-importer@example.com",
        "equipment.view",
        "equipment.import",
    )
    existing_asset_id = _create_equipment_asset(app, asset_tag="EXIST-001")

    with app.app_context():
        import_user = User(
            email="import.user@example.com",
            password=generate_password_hash("pass"),
            active=True,
            display_name="Import User",
        )
        db.session.add(import_user)
        db.session.commit()

    csv_payload = (
        "Asset Tag,Name,Serial Number,Category,Manufacturer,Model,Status,Purchase Date,Purchase Cost,Supplier,Location,Assigned To,Notes,PO Number,Invoice Number\n"
        "NEW-001,Front Counter Printer,SER-NEW-001,POS,Epson,TM-T88,Ready,2026-04-02,499.99,Import Vendor,Import Room,import.user@example.com,Installed at front,PO-700,INV-700\n"
        "EXIST-001,Updated Existing Asset,SER-EXIST-001,Cooling,True,T-49F,Broken,2026-04-03,1299.50,Import Vendor,Import Room,import.user@example.com,Needs repair,PO-701,INV-701\n"
    )

    with client:
        login(client, importer_email, "pass")
        import_response = client.post(
            "/equipment/import/snipe-it",
            data={
                "default_category_name": "Imported Equipment",
                "create_missing_locations": "y",
                "update_existing": "y",
                "file": (BytesIO(csv_payload.encode("utf-8")), "snipe_it.csv"),
            },
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert import_response.status_code == 200
        assert b"1 created, 1 updated, 0 skipped." in import_response.data

    with app.app_context():
        new_asset = EquipmentAsset.query.filter_by(asset_tag="NEW-001").one()
        updated_asset = db.session.get(EquipmentAsset, existing_asset_id)
        import_location = Location.query.filter_by(name="Import Room").one()
        import_batches = EquipmentIntakeBatch.query.filter_by(
            source_type=EquipmentIntakeBatch.SOURCE_SNIPE_IT
        ).all()

        assert new_asset.serial_number == "SER-NEW-001"
        assert new_asset.location_id == import_location.id
        assert new_asset.home_location_id == import_location.id
        assert new_asset.equipment_intake_batch_id is not None
        assert updated_asset.name == "Updated Existing Asset"
        assert updated_asset.serial_number == "SER-EXIST-001"
        assert updated_asset.status == EquipmentAsset.STATUS_OUT_OF_SERVICE
        assert updated_asset.equipment_intake_batch_id is not None
        assert len(import_batches) == 2

        flush_activity_logs()
        activities = [entry.activity for entry in ActivityLog.query.all()]
        assert any(
            "Imported equipment from Snipe-IT CSV" in activity
            for activity in activities
        )


def test_equipment_label_print_route_returns_pdf_and_logs_activity(
    client, app, monkeypatch
):
    printer_email = _create_user(
        app,
        "equipment-printer@example.com",
        "equipment.view",
        "equipment.print_labels",
    )
    asset_id = _create_equipment_asset(app, asset_tag="LBL-001")

    monkeypatch.setattr(
        "app.routes.equipment_routes.render_equipment_label_pdf",
        lambda assets, qr_payloads: b"%PDF-FAKE\nlabel",
    )

    with client:
        login(client, printer_email, "pass")
        missing = client.get("/equipment/labels/print")
        assert missing.status_code == 400

        response = client.get(f"/equipment/labels/print?equipment_id={asset_id}")
        assert response.status_code == 200
        assert response.mimetype == "application/pdf"
        assert response.data.startswith(b"%PDF-FAKE")
        assert "inline; filename=LBL-001-label.pdf" == response.headers[
            "Content-Disposition"
        ]

    with app.app_context():
        flush_activity_logs()
        activities = [entry.activity for entry in ActivityLog.query.all()]
        assert any(
            "Printed equipment label(s) for assets" in activity
            for activity in activities
        )


def test_equipment_custody_scan_flow_and_qr_targets(client, app, monkeypatch):
    manager_email = _create_user(
        app,
        "equipment-custody-manager@example.com",
        "equipment.view",
        "equipment.edit",
        "equipment.manage_custody",
        "equipment.print_labels",
    )
    asset_id = _create_equipment_asset(app, asset_tag="CHK-001")
    captured_payloads = {}

    def _fake_render(assets, qr_payloads):
        captured_payloads.update(qr_payloads)
        return b"%PDF-FAKE\ncustody"

    monkeypatch.setattr(
        "app.routes.equipment_routes.render_equipment_label_pdf",
        _fake_render,
    )

    with client:
        login(client, manager_email, "pass")

        scan_page = client.get(f"/equipment/{asset_id}/scan")
        assert scan_page.status_code == 200
        assert b"Sign Out To Me" in scan_page.data

        checkout = client.post(
            f"/equipment/{asset_id}/check-out",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert checkout.status_code == 200
        assert b"Equipment checked out." in checkout.data
        assert b"Sign In" in checkout.data

        checkin = client.post(
            f"/equipment/{asset_id}/check-in",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert checkin.status_code == 200
        assert b"Equipment checked in." in checkin.data
        assert b"Sign Out To Me" in checkin.data

        with app.app_context():
            asset = db.session.get(EquipmentAsset, asset_id)
            asset.label_qr_target = EquipmentAsset.QR_TARGET_SCAN
            db.session.commit()

        label_response = client.get(f"/equipment/labels/print?equipment_id={asset_id}")
        assert label_response.status_code == 200
        assert label_response.data.startswith(b"%PDF-FAKE")

    with app.app_context():
        asset = db.session.get(EquipmentAsset, asset_id)
        user = User.query.filter_by(email=manager_email).one()
        events = (
            EquipmentCustodyEvent.query.filter_by(equipment_asset_id=asset_id)
            .order_by(EquipmentCustodyEvent.created_at.asc())
            .all()
        )

        assert asset.checked_out_at is None
        assert asset.location_id == asset.home_location_id
        assert asset.assigned_user_id is None
        assert len(events) == 2
        assert events[0].action == EquipmentCustodyEvent.ACTION_CHECK_OUT
        assert events[0].to_assigned_user_id == user.id
        assert events[1].action == EquipmentCustodyEvent.ACTION_CHECK_IN
        assert events[1].to_location_id == asset.home_location_id
        assert captured_payloads[asset_id].endswith(f"/equipment/{asset_id}/scan")

        flush_activity_logs()
        activities = [entry.activity for entry in ActivityLog.query.all()]
        assert any("Checked out equipment CHK-001" in activity for activity in activities)
        assert any("Checked in equipment CHK-001" in activity for activity in activities)


def test_equipment_custody_permission_split_and_scan_visibility(client, app):
    viewer_email = _create_user(app, "equipment-custody-viewer@example.com", "equipment.view")
    custody_email = _create_user(
        app,
        "equipment-custody-only@example.com",
        "equipment.manage_custody",
    )
    outsider_email = _create_user(app, "equipment-custody-outsider@example.com")
    asset_id = _create_equipment_asset(app, asset_tag="SCAN-001")

    with client:
        login(client, viewer_email, "pass")
        detail_page = client.get(f"/equipment/{asset_id}")
        assert detail_page.status_code == 200
        detail_html = detail_page.get_data(as_text=True)
        assert "Scan Page" not in detail_html
        assert "Sign Out" not in detail_html
        assert client.get(f"/equipment/{asset_id}/scan").status_code == 403
        assert (
            client.post(f"/equipment/{asset_id}/check-out", data={"submit": "1"}).status_code
            == 403
        )

        login(client, custody_email, "pass")
        assert client.get("/equipment").status_code == 403
        scan_page = client.get(f"/equipment/{asset_id}/scan")
        assert scan_page.status_code == 200
        assert b"Sign Out To Me" in scan_page.data

        checkout = client.post(
            f"/equipment/{asset_id}/check-out",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert checkout.status_code == 200
        assert b"Equipment checked out." in checkout.data

        checkin = client.post(
            f"/equipment/{asset_id}/check-in",
            data={"submit": "1"},
            follow_redirects=True,
        )
        assert checkin.status_code == 200
        assert b"Equipment checked in." in checkin.data

        login(client, outsider_email, "pass")
        assert client.get(f"/equipment/{asset_id}/scan").status_code == 403


def test_render_equipment_label_pdf_returns_pdf_bytes():
    asset = SimpleNamespace(
        id=101,
        asset_tag="LBL-REAL-001",
        display_name="Front Counter Printer",
        model_display_name="Epson TM-T88VII",
        serial_number="SER-LBL-REAL-001",
        status_label="Operational",
        location_label="Front Counter / Drawer 1",
        custodian_label="Cash Office",
    )

    pdf_bytes = render_equipment_label_pdf(
        [asset],
        {asset.id: "https://example.com/equipment/101"},
    )

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000
