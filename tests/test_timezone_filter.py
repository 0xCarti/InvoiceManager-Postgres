from datetime import date, datetime, timezone

from flask_login import login_user, logout_user

import app as app_module
from app import db
from app.models import Setting, User


def test_format_datetime_uses_user_and_default_timezone(app):
    with app.app_context():
        setting = Setting.query.filter_by(name="DEFAULT_TIMEZONE").first()
        setting.value = "UTC"
        db.session.commit()
        app_module.DEFAULT_TIMEZONE = "UTC"

        user = User(
            email="tzf@example.com",
            password="pass",
            active=True,
            timezone="US/Eastern",
        )
        db.session.add(user)
        db.session.commit()
        fmt = app.jinja_env.filters["format_datetime"]
        dt = datetime(2023, 1, 1, tzinfo=timezone.utc)
        with app.test_request_context():
            login_user(user)
            assert fmt(dt, "%Y-%m-%d %H:%M") == "2022-12-31 19:00"
            logout_user()

        user.timezone = None
        db.session.commit()
        setting.value = "US/Central"
        db.session.commit()
        app_module.DEFAULT_TIMEZONE = "US/Central"
        with app.test_request_context():
            login_user(user)
            assert fmt(dt, "%Y-%m-%d %H:%M") == "2022-12-31 18:00"
            logout_user()


def test_format_datetime_localizes_naive_values(app):
    with app.app_context():
        user = User(
            email="localtz@example.com",
            password="pass",
            active=True,
            timezone="America/Winnipeg",
        )
        db.session.add(user)
        db.session.commit()

        fmt = app.jinja_env.filters["format_datetime"]

        naive_date = date(2024, 3, 15)
        naive_datetime = datetime(2024, 3, 15, 5, 0, 0)

        with app.test_request_context():
            login_user(user)

            assert fmt(naive_date, "%Y-%m-%d %H:%M") == "2024-03-15 00:00"
            assert fmt(naive_datetime, "%Y-%m-%d %H:%M") == "2024-03-15 05:00"

            logout_user()
