import os

from app import create_app, create_admin_user, db
from app.models import Setting


def seed_initial_data() -> None:
    """Seed the database with an admin user and default settings."""
    app, _ = create_app([])
    with app.app_context():
        create_admin_user()
        gst_value = os.getenv("GST", "")
        tz_value = os.getenv("DEFAULT_TIMEZONE", "UTC")

        gst_setting = Setting.query.filter_by(name="GST").first()
        if gst_setting is None:
            gst_setting = Setting(name="GST", value=gst_value)
            db.session.add(gst_setting)
        else:
            gst_setting.value = gst_value

        tz_setting = Setting.query.filter_by(name="DEFAULT_TIMEZONE").first()
        if tz_setting is None:
            tz_setting = Setting(name="DEFAULT_TIMEZONE", value=tz_value)
            db.session.add(tz_setting)
        else:
            tz_setting.value = tz_value

        db.session.commit()
        print("Initial admin user and settings created.")


if __name__ == "__main__":
    seed_initial_data()
