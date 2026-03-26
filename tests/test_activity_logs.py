import os
from datetime import datetime, timedelta

from flask import url_for
from werkzeug.security import generate_password_hash

from app import db
from app.models import ActivityLog, User
from tests.utils import login


def create_log(app):
    with app.app_context():
        user = User(
            email="log@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        log = ActivityLog(user_id=user.id, activity="Did something")
        db.session.add(log)
        db.session.commit()
        return log.activity


def test_admin_can_view_activity_logs(client, app):
    text = create_log(app)
    with app.app_context():
        with app.test_request_context():
            expected = url_for("admin.activity_logs")

    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")
    with client:
        login(client, admin_email, admin_pass)
        resp = client.get(expected, follow_redirects=True)
        assert resp.status_code == 200
        assert text.encode() in resp.data


def test_non_admin_forbidden_from_activity_logs(client, app):
    with app.app_context():
        user = User(
            email="normal@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(user)
        db.session.commit()

    with client:
        login(client, "normal@example.com", "pass")
        resp = client.get("/controlpanel/activity")
        assert resp.status_code == 403


def test_activity_log_filters(client, app):
    admin_email = os.getenv("ADMIN_EMAIL", "admin@example.com")
    admin_pass = os.getenv("ADMIN_PASS", "adminpass")

    with app.app_context():
        admin_user = User.query.filter_by(email=admin_email).first()
        admin_id = admin_user.id
        other_user = User(
            email="other@example.com",
            password=generate_password_hash("pass"),
            active=True,
        )
        db.session.add(other_user)
        db.session.commit()

        base_time = datetime(2024, 1, 1, 12, 0, 0)
        db.session.add_all(
            [
                ActivityLog(
                    user_id=admin_user.id,
                    activity="Alpha admin action",
                    timestamp=base_time,
                ),
                ActivityLog(
                    user_id=other_user.id,
                    activity="Beta user action",
                    timestamp=base_time + timedelta(days=1),
                ),
                ActivityLog(
                    user_id=None,
                    activity="Gamma system event",
                    timestamp=base_time + timedelta(days=2),
                ),
            ]
        )
        db.session.commit()

        with app.test_request_context():
            activity_url = url_for("admin.activity_logs")

    with client:
        login(client, admin_email, admin_pass)

        # Filter by a specific user
        resp = client.get(f"{activity_url}?user_id={admin_id}")
        assert resp.status_code == 200
        assert b"Alpha admin action" in resp.data
        assert b"Beta user action" not in resp.data
        assert b"Gamma system event" not in resp.data

        # Filter for system-generated entries
        resp = client.get(f"{activity_url}?user_id=-2")
        assert resp.status_code == 200
        assert b"Gamma system event" in resp.data
        assert b"Alpha admin action" not in resp.data
        assert b"Beta user action" not in resp.data

        # Filter by partial activity text
        resp = client.get(f"{activity_url}?activity=beta")
        assert resp.status_code == 200
        assert b"Beta user action" in resp.data
        assert b"Alpha admin action" not in resp.data
        assert b"Gamma system event" not in resp.data

        # Filter by date range
        filter_date = (base_time + timedelta(days=1)).date().isoformat()
        resp = client.get(
            f"{activity_url}?start_date={filter_date}&end_date={filter_date}"
        )
        assert resp.status_code == 200
        assert b"Beta user action" in resp.data
        assert b"Alpha admin action" not in resp.data
        assert b"Gamma system event" not in resp.data
