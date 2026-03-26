import os
import shutil
import sqlite3
import time
from datetime import date
import json
from io import BytesIO

from werkzeug.security import generate_password_hash

from app import db
from app.forms import MAX_BACKUP_SIZE
from app.models import (
    ActivityLog,
    Event,
    EventLocation,
    EventStandSheetItem,
    GLCode,
    Item,
    ItemUnit,
    Location,
    Product,
    ProductRecipeItem,
    PurchaseInvoice,
    PurchaseInvoiceDraft,
    PurchaseInvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderItemArchive,
    TerminalSale,
    User,
    Vendor,
    Setting,
)
from app.utils.activity import flush_activity_logs
from app.utils.backup import (
    BACKUP_SCHEMA_VERSION,
    _backup_loop,
    create_backup,
    restore_backup,
    validate_backup_file_compatibility,
    validate_restored_backup_compatibility,
)
from tests.utils import login


def _create_sqlite_backup_copy(app, filename):
    with app.app_context():
        generated = create_backup()
        source = os.path.join(app.config["BACKUP_FOLDER"], generated)
        destination = os.path.join(app.config["BACKUP_FOLDER"], filename)
        shutil.copyfile(source, destination)
    return destination


def populate_data():
    gl = GLCode(code="6000")
    item = Item(name="BackupItem", base_unit="each")
    unit = ItemUnit(
        item=item,
        name="each",
        factor=1,
        receiving_default=True,
        transfer_default=True,
    )
    vendor = Vendor(first_name="Back", last_name="Vendor")
    location = Location(name="BackupLoc")
    user = User(
        email="backup@example.com",
        password=generate_password_hash("pass"),
        active=True,
    )
    db.session.add_all([gl, item, unit, vendor, location, user])
    db.session.commit()

    product = Product(name="BackupProduct", price=1.0, cost=0.5, gl_code="6000")
    recipe = ProductRecipeItem(
        product=product,
        item=item,
        unit=unit,
        quantity=1,
        countable=True,
    )
    db.session.add_all([product, recipe])

    po = PurchaseOrder(
        vendor_id=vendor.id,
        user_id=user.id,
        order_date=date(2023, 1, 1),
        expected_date=date(2023, 1, 2),
        delivery_charge=0,
    )
    db.session.add(po)
    db.session.flush()

    poi = PurchaseOrderItem(purchase_order=po, item=item, unit=unit, quantity=1)
    archive = PurchaseOrderItemArchive(
        purchase_order_id=po.id,
        item_id=item.id,
        unit_id=unit.id,
        quantity=1,
    )
    invoice = PurchaseInvoice(
        purchase_order=po,
        user_id=user.id,
        location=location,
        received_date=date(2023, 1, 3),
        invoice_number="VN001",
        gst=0.1,
        pst=0.2,
        delivery_charge=1.0,
    )
    pii = PurchaseInvoiceItem(
        invoice=invoice,
        item=item,
        unit=unit,
        item_name=item.name,
        unit_name=unit.name,
        quantity=1,
        cost=2.0,
    )
    event = Event(
        name="BackupEvent",
        start_date=date(2023, 2, 1),
        end_date=date(2023, 2, 2),
        event_type="inventory",
    )
    event_loc = EventLocation(event=event, location=location)
    sale = TerminalSale(event_location=event_loc, product=product, quantity=5)
    stand_item = EventStandSheetItem(
        event_location=event_loc, item=item, opening_count=0, closing_count=0
    )

    draft = PurchaseInvoiceDraft(
        purchase_order_id=po.id,
        payload=json.dumps(
            {
                "invoice_number": "VN001",
                "received_date": "2023-01-03",
                "location_id": location.id,
                "gst": 0.1,
                "pst": 0.2,
                "delivery_charge": 1.0,
                "items": [
                    {
                        "item_id": item.id,
                        "unit_id": unit.id,
                        "quantity": 1,
                        "cost": 2.0,
                        "position": 0,
                        "gl_code_id": None,
                        "location_id": None,
                    }
                ],
            }
        ),
    )

    db.session.add_all(
        [
            poi,
            archive,
            invoice,
            pii,
            event,
            event_loc,
            sale,
            stand_item,
            draft,
        ]
    )
    db.session.commit()

    models = [
        GLCode,
        Item,
        ItemUnit,
        Product,
        ProductRecipeItem,
        Vendor,
        Location,
        User,
        PurchaseOrder,
        PurchaseOrderItem,
        PurchaseOrderItemArchive,
        PurchaseInvoice,
        PurchaseInvoiceItem,
        PurchaseInvoiceDraft,
        Event,
        EventLocation,
        TerminalSale,
        EventStandSheetItem,
    ]
    return {m: m.query.count() for m in models}, models


def test_backup_and_restore(app):
    with app.app_context():
        counts, models = populate_data()
        engine_url_before = str(db.engine.url)

        filename = create_backup()
        backup_path = os.path.join(app.config["BACKUP_FOLDER"], filename)
        assert os.path.exists(backup_path)

        for m in models:
            m.query.delete()
        db.session.commit()

        restore_backup(backup_path)

        # Restore should mutate the active database without changing its engine target
        assert str(db.engine.url) == engine_url_before

        for m, count in counts.items():
            assert m.query.count() == count


def test_restore_backup_file_rejects_path_traversal(client, app):
    with app.app_context():
        admin = User.query.filter_by(is_admin=True).first()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(admin.id)
        sess["_fresh"] = True
    resp = client.post("/controlpanel/backups/restore/../../etc/passwd")
    assert resp.status_code == 404


def test_backup_retention(app):
    with app.app_context():
        backups_dir = app.config["BACKUP_FOLDER"]
        for f in os.listdir(backups_dir):
            os.remove(os.path.join(backups_dir, f))
        app.config["MAX_BACKUPS"] = 2
        for _ in range(3):
            create_backup()
            time.sleep(1)
        files = sorted(os.listdir(backups_dir))
        assert len(files) == 2


def test_auto_backup_activity_logging(app):
    with app.app_context():
        backups_dir = app.config["BACKUP_FOLDER"]
        for f in os.listdir(backups_dir):
            os.remove(os.path.join(backups_dir, f))

        ActivityLog.query.delete()
        db.session.commit()

        app.config["MAX_BACKUPS"] = 1

        filename1 = create_backup(initiated_by_system=True)
        flush_activity_logs()

        logs = [log.activity for log in ActivityLog.query.order_by(ActivityLog.id)]
        assert logs[-1] == f"System automatically created backup {filename1}"

        time.sleep(1)
        filename2 = create_backup(initiated_by_system=True)
        flush_activity_logs()

        logs = [log.activity for log in ActivityLog.query.order_by(ActivityLog.id)]
        assert f"System automatically deleted backup {filename1}" in logs
        assert logs[-1] == f"System automatically created backup {filename2}"


def test_create_backup_is_atomic(app, monkeypatch):
    with app.app_context():
        backups_dir = app.config["BACKUP_FOLDER"]
        for f in os.listdir(backups_dir):
            os.remove(os.path.join(backups_dir, f))

        recorded = {}

        real_copyfile = shutil.copyfile
        real_replace = os.replace

        def recording_copyfile(src, dst, *args, **kwargs):
            recorded["copy_dst"] = dst
            return real_copyfile(src, dst, *args, **kwargs)

        def recording_replace(src, dst, *args, **kwargs):
            recorded["replace_src"] = src
            recorded["replace_dst"] = dst
            return real_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(shutil, "copyfile", recording_copyfile)
        monkeypatch.setattr(os, "replace", recording_replace)

        filename = create_backup()
        backup_path = os.path.join(backups_dir, filename)

        assert os.path.exists(backup_path)
        assert recorded["copy_dst"] != backup_path
        assert recorded["replace_dst"] == backup_path
        assert not os.path.exists(recorded["replace_src"])


def test_backup_loop_runs_on_interval(app, monkeypatch):
    from app.utils import backup as backup_module

    call_times: list[float] = []
    wait_calls: list[float] = []
    now = {"value": 0.0}

    class DummyEvent:
        def __init__(self):
            self._is_set = False

        def wait(self, timeout):
            if self._is_set:
                return True
            wait_calls.append(timeout)
            if timeout > 0:
                now["value"] += timeout
            return False

        def set(self):
            self._is_set = True

        def is_set(self):
            return self._is_set

    stop_event = DummyEvent()

    def fake_create_backup(*, initiated_by_system=False):
        call_times.append(now["value"])
        now["value"] += 120  # backups take two minutes
        if len(call_times) >= 3:
            stop_event.set()

    def fake_monotonic():
        return now["value"]

    monkeypatch.setattr(backup_module, "_stop_event", stop_event)
    monkeypatch.setattr(backup_module, "create_backup", fake_create_backup)
    monkeypatch.setattr(backup_module.time, "monotonic", fake_monotonic)

    _backup_loop(app, 3600)

    assert call_times == [3600, 7200, 10800]
    assert len(wait_calls) >= 3
    assert wait_calls[0] == 3600
    assert all(call > 0 for call in wait_calls[:3])


def test_restore_backup_route_rejects_large_file(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        big_content = b"a" * (MAX_BACKUP_SIZE + 1)
        data = {"file": (BytesIO(big_content), "large.db")}
        resp = client.post(
            "/controlpanel/backups/restore",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"File is too large." in resp.data


def test_restore_backup_route_rejects_invalid_sqlite(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        data = {"file": (BytesIO(b"not a sqlite"), "bad.db")}
        resp = client.post(
            "/controlpanel/backups/restore",
            data=data,
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        assert b"Invalid SQLite database." in resp.data


def test_validate_backup_file_compatibility_handles_missing_setting_table(app):
    backup_path = _create_sqlite_backup_copy(
        app, "missing_setting_table_preflight.db"
    )

    with sqlite3.connect(backup_path) as conn:
        conn.execute("DROP TABLE setting")
        conn.commit()

    with app.app_context():
        result = validate_backup_file_compatibility(backup_path)

    assert result.compatible is False
    assert any("Missing required tables: setting." in issue for issue in result.issues)
    assert any("Missing setting table." in warning for warning in result.warnings)


def test_restore_backup_route_missing_setting_table_shows_compatibility_error(
    client, app
):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        backup_path = _create_sqlite_backup_copy(app, "missing_setting_table_route.db")

    with sqlite3.connect(backup_path) as conn:
        conn.execute("DROP TABLE setting")
        conn.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/missing_setting_table_route.db",
            follow_redirects=True,
        )

    assert b"Incompatible backup" in response.data
    assert b"Invalid SQLite database." not in response.data


def test_create_backup_persists_schema_version_marker(app):
    with app.app_context():
        Setting.query.filter_by(name="APP_SCHEMA_VERSION").delete()
        db.session.commit()

        create_backup()

        marker = Setting.query.filter_by(name="APP_SCHEMA_VERSION").first()
        assert marker is not None
        assert marker.value == BACKUP_SCHEMA_VERSION


def test_restore_compatibility_detects_missing_marker(app):
    with app.app_context():
        db.create_all()
        Setting.query.filter_by(name="APP_SCHEMA_VERSION").delete()
        db.session.commit()

        result = validate_restored_backup_compatibility()

        assert result.compatible is False
        assert any("APP_SCHEMA_VERSION" in issue for issue in result.issues)


def test_restore_compatibility_detects_missing_menu_endpoint(app):
    with app.app_context():
        db.create_all()

        removed = app.view_functions.pop("menu.view_menus", None)
        try:
            result = validate_restored_backup_compatibility()
        finally:
            if removed is not None:
                app.view_functions["menu.view_menus"] = removed

        assert result.compatible is False
        assert any("menu.view_menus" in issue for issue in result.issues)


def test_restore_backup_file_logs_warning_restore_on_missing_marker(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    backup_path = _create_sqlite_backup_copy(app, "marker_warning.db")

    with sqlite3.connect(backup_path) as conn:
        conn.execute(
            "DELETE FROM setting WHERE name = ?",
            ("APP_SCHEMA_VERSION",),
        )
        conn.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/marker_warning.db",
            follow_redirects=True,
        )

    assert b"Restored with compatibility warnings." in response.data
    assert b"Backup restored from marker_warning.db" in response.data

    with app.app_context():
        flush_activity_logs()
        activities = [
            row.activity for row in ActivityLog.query.order_by(ActivityLog.id).all()
        ]
        assert not any(
            "Restore blocked due to compatibility errors" in a for a in activities
        )
        assert any(
            "Restored backup marker_warning.db with compatibility warnings" in a
            for a in activities
        )


def test_restore_with_marker_warning_mutates_live_db_from_backup(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        live_user = User(
            email="live-only@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(live_user)
        db.session.commit()

    backup_path = _create_sqlite_backup_copy(app, "marker_warning_state_check.db")

    with sqlite3.connect(backup_path) as conn:
        conn.execute("DELETE FROM setting WHERE name = ?", ("APP_SCHEMA_VERSION",))
        conn.execute("DELETE FROM user WHERE email = ?", ("live-only@example.com",))
        conn.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/marker_warning_state_check.db",
            follow_redirects=True,
        )

    assert b"Restored with compatibility warnings." in response.data

    with app.app_context():
        assert User.query.filter_by(email="live-only@example.com").count() == 0


def test_restore_with_older_schema_marker_value_proceeds(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    backup_path = _create_sqlite_backup_copy(app, "older_marker.db")

    with sqlite3.connect(backup_path) as conn:
        conn.execute(
            "UPDATE setting SET value = ? WHERE name = ?",
            ("2025.12", "APP_SCHEMA_VERSION"),
        )
        conn.commit()

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/older_marker.db",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Restored with compatibility warnings." in response.data
    assert b"Backup restored from older_marker.db" in response.data


def test_restore_backup_prunes_invalid_favorites_and_backups_page_loads(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).first()
        assert admin_user is not None
        admin_user.favorites = "admin.backups,missing.endpoint,item.view_items"
        db.session.commit()
    backup_path = _create_sqlite_backup_copy(app, "favorites_prune.db")

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/favorites_prune.db",
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert b"Favorites mode: pruned invalid favorites" in response.data

    with app.app_context():
        restored_admin = User.query.filter_by(email=admin_email).first()
        assert restored_admin is not None
        assert restored_admin.favorites == "admin.backups,item.view_items"


def test_restore_backup_ignore_favorites_clears_all_user_favorites(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).first()
        assert admin_user is not None
        admin_user.favorites = "admin.backups,item.view_items"
        db.session.commit()
    backup_path = _create_sqlite_backup_copy(app, "favorites_ignore.db")

    with client:
        login(client, admin_email, admin_pass)
        response = client.post(
            "/controlpanel/backups/restore/favorites_ignore.db",
            data={"ignore_favorites": "1"},
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert (
        b"Favorites mode: ignored backup favorites and cleared all user favorites"
        in response.data
    )

    with app.app_context():
        restored_admin = User.query.filter_by(email=admin_email).first()
        assert restored_admin is not None
        assert restored_admin.favorites == ""
