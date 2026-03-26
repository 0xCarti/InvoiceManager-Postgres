from datetime import date, datetime, timezone

from flask_login import login_user, logout_user

from app import db
from app.models import Event, User
from app.services import dashboard_metrics
from app.services import event_service


def test_event_schedule_uses_user_timezone(app, monkeypatch):
    with app.app_context():
        user = User(
            email="tzuser@example.com",
            password="pass",
            active=True,
            timezone="America/Winnipeg",
        )
        db.session.add(user)
        db.session.commit()

        event = Event(
            name="Tomorrow's Fair",
            start_date=date(2024, 3, 15),
            end_date=date(2024, 3, 15),
        )
        db.session.add(event)
        db.session.commit()

        fake_now = datetime(2024, 3, 15, 2, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(event_service, "_utcnow", lambda: fake_now)

        with app.test_request_context():
            login_user(user)
            summary = dashboard_metrics.event_summary()
            schedule = event_service.event_schedule()
            logout_user()

        assert summary["today_count"] == 0
        assert summary["upcoming_count"] == 1
        assert schedule["calendar"]["today"] == date(2024, 3, 14)
        assert schedule["events"][0]["status"] == "upcoming"


def test_current_user_today_uses_config_default_timezone(app, monkeypatch):
    with app.app_context():
        app.config["DEFAULT_TIMEZONE"] = "Pacific/Auckland"

        user = User(
            email="defaulttz@example.com",
            password="pass",
            active=True,
        )
        db.session.add(user)
        db.session.commit()

        fake_now = datetime(2024, 3, 15, 11, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(event_service, "_utcnow", lambda: fake_now)

        with app.test_request_context():
            login_user(user)
            today = event_service.current_user_today()
            logout_user()

        assert today == date(2024, 3, 16)


def test_dashboard_today_format_preserves_user_timezone(app, monkeypatch):
    with app.app_context():
        user = User(
            email="dashboardtz@example.com",
            password="pass",
            active=True,
            timezone="America/Winnipeg",
        )
        db.session.add(user)
        db.session.commit()

        fake_now = datetime(2024, 3, 15, 3, 0, tzinfo=timezone.utc)
        monkeypatch.setattr(event_service, "_utcnow", lambda: fake_now)

        fmt = app.jinja_env.filters["format_datetime"]

        with app.test_request_context():
            login_user(user)
            today_label = fmt(event_service.event_schedule()["calendar"]["today"], "%Y-%m-%d")
            logout_user()

        assert today_label == "2024-03-14"
