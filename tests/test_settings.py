import os
from threading import Event

from app import db
from app.models import ActivityLog, Setting, User
from app.utils.activity import flush_activity_logs
from app.utils.backup import UNIT_SECONDS
from app.utils.units import parse_conversion_setting
from tests.permission_helpers import grant_permissions
from tests.utils import login
from werkzeug.security import generate_password_hash


def test_admin_can_update_settings(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with app.app_context():
        # ensure default setting exists
        setting = Setting.query.filter_by(name="GST").first()
        assert setting is not None
        setting.value = ""
        db.session.commit()
    with client:
        login(client, admin_email, admin_pass)
        resp = client.post(
            "/controlpanel/settings",
            data={
                "gst_number": "987654321",
                "default_timezone": "US/Eastern",
                "auto_backup_enabled": "y",
                "auto_backup_interval_value": "2",
                "auto_backup_interval_unit": "week",
                "pos_sales_import_interval_value": "6",
                "pos_sales_import_interval_unit": "hour",
                "max_backups": "5",
                "enable_sysco_imports": "y",
                "enable_manitoba_liquor_imports": "y",
                "convert_ounce": "gram",
                "convert_gram": "ounce",
                "convert_each": "each",
                "convert_millilitre": "ounce",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200
    with app.app_context():
        setting = Setting.query.filter_by(name="GST").first()
        assert setting.value == "987654321"
        from app import GST

        assert GST == "987654321"
        tz_setting = Setting.query.filter_by(name="DEFAULT_TIMEZONE").first()
        assert tz_setting.value == "US/Eastern"
        from app import DEFAULT_TIMEZONE

        assert DEFAULT_TIMEZONE == "US/Eastern"
        auto_setting = Setting.query.filter_by(
            name="AUTO_BACKUP_ENABLED"
        ).first()
        assert auto_setting.value == "1"
        interval_value = Setting.query.filter_by(
            name="AUTO_BACKUP_INTERVAL_VALUE"
        ).first()
        assert interval_value.value == "2"
        interval_unit = Setting.query.filter_by(
            name="AUTO_BACKUP_INTERVAL_UNIT"
        ).first()
        assert interval_unit.value == "week"
        pos_sales_interval = Setting.get_pos_sales_import_interval()
        assert pos_sales_interval == {"value": 6, "unit": "hour"}
        max_setting = Setting.query.filter_by(name="MAX_BACKUPS").first()
        assert max_setting.value == "5"
        conversion_setting = Setting.query.filter_by(
            name="BASE_UNIT_CONVERSIONS"
        ).first()
        enabled_import_vendors = Setting.get_enabled_purchase_import_vendors()
        mapping = parse_conversion_setting(conversion_setting.value)
        assert mapping == {
            "ounce": "gram",
            "gram": "ounce",
            "each": "each",
            "millilitre": "ounce",
        }
        assert enabled_import_vendors == [
            "SYSCO",
            "MANITOBA LIQUOR & LOTTERIES",
        ]
        assert app.config["AUTO_BACKUP_ENABLED"] is True
        assert app.config["AUTO_BACKUP_INTERVAL_VALUE"] == 2
        assert app.config["AUTO_BACKUP_INTERVAL_UNIT"] == "week"
        assert app.config["POS_SALES_IMPORT_INTERVAL_VALUE"] == 6
        assert app.config["POS_SALES_IMPORT_INTERVAL_UNIT"] == "hour"
        assert app.config["AUTO_BACKUP_INTERVAL"] == 2 * UNIT_SECONDS["week"]
        assert app.config["MAX_BACKUPS"] == 5
        assert app.config["BASE_UNIT_CONVERSIONS"] == mapping
        flush_activity_logs()
        activities = [row.activity for row in ActivityLog.query.order_by(ActivityLog.id).all()]
        assert any(
            "Updated settings:" in activity
            and "POS sales import cadence" in activity
            for activity in activities
        )


def test_auto_backup_thread_uses_real_app(client, app, monkeypatch):
    from app.utils import backup as backup_module

    calls = {}

    def fake_loop(app_obj, interval):
        with app_obj.app_context():
            calls["entered_context"] = True
            calls["interval"] = interval

    class ImmediateThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target = target
            self._args = args
            self.daemon = daemon

        def is_alive(self):
            return False

        def join(self):
            return None

        def start(self):
            if self._target:
                self._target(*self._args)

    monkeypatch.setattr(backup_module, "_backup_loop", fake_loop)
    monkeypatch.setattr(backup_module, "Thread", ImmediateThread)
    monkeypatch.setattr(backup_module, "_backup_thread", None)
    monkeypatch.setattr(backup_module, "_stop_event", Event())

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with client:
        login(client, admin_email, admin_pass)
        resp = client.post(
            "/controlpanel/settings",
            data={
                "gst_number": "",  # keep defaults minimal for this test
                "default_timezone": "UTC",
                "auto_backup_enabled": "y",
                "auto_backup_interval_value": "1",
                "auto_backup_interval_unit": "day",
                "pos_sales_import_interval_value": "1",
                "pos_sales_import_interval_unit": "day",
                "max_backups": "3",
                "enable_sysco_imports": "y",
                "convert_ounce": "gram",
                "convert_gram": "ounce",
                "convert_each": "each",
                "convert_millilitre": "ounce",
            },
            follow_redirects=True,
        )
        assert resp.status_code == 200

    assert calls.get("entered_context") is True
    assert calls.get("interval") == UNIT_SECONDS["day"]


def test_settings_view_only_user_sees_read_only_page(client, app):
    with app.app_context():
        user = User(
            email="settings-viewer@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        grant_permissions(
            user,
            "settings.view",
            group_name="Settings View Only",
            description="Can review settings but not update them.",
        )

    with client:
        login(client, "settings-viewer@example.com", "pass")
        response = client.get("/controlpanel/settings", follow_redirects=True)

    assert response.status_code == 200
    assert b"You have view-only access to settings." in response.data
    assert b">Update<" not in response.data
    assert b"disabled" in response.data
