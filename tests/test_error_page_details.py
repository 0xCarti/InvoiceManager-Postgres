import os

import pytest
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app import _build_user_error_details, _redact_error_details, db
from app.models import User


def test_redact_error_details_masks_common_secret_patterns():
    raw = (
        "password=hunter2\n"
        "Authorization: Bearer abc.def.ghi\n"
        "postgresql://dbuser:dbpass@localhost:5432/app\n"
        "Cookie: sessionid=abcd1234\n"
    )

    redacted = _redact_error_details(raw)

    assert "hunter2" not in redacted
    assert "abc.def.ghi" not in redacted
    assert "dbpass" not in redacted
    assert "sessionid=abcd1234" not in redacted
    assert "<redacted>" in redacted


def test_error_page_hides_trace_details_by_default(client, app):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["SHOW_ERROR_DETAILS_TO_USERS"] = False

    @app.route("/__explode_default")
    def _explode_default():
        raise RuntimeError("password=supersecret")

    response = client.get("/__explode_default")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Detailed traceback is hidden for safety." in body
    assert "supersecret" not in body
    assert "Do not share this publicly" in body
    assert "Copy for support" in body


def test_error_page_support_mode_shows_detailed_trace_with_truncation(client, app):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.config["SHOW_ERROR_DETAILS_TO_USERS"] = True
    app.config["ERROR_DETAILS_MAX_LENGTH"] = 120

    @app.route("/__explode_support")
    def _explode_support():
        raise RuntimeError("token=abcd1234 " + ("x" * 600))

    response = client.get("/__explode_support")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "...[truncated]..." in body
    assert "See token" in body
    assert "abcd1234" not in body


def test_build_user_error_details_summary_uses_last_trace_line():
    output = _build_user_error_details(
        "Traceback...\nRuntimeError: secret=hello",
        show_detailed_trace=False,
        max_length=1000,
        error_token="deadbeef",
    )

    assert "RuntimeError: secret=<redacted>" in output
    assert "Share token deadbeef with support for full logs." in output


def test_unhandled_exception_recovers_from_failed_transaction_for_user_logging(
    client, app
):
    app.config["PROPAGATE_EXCEPTIONS"] = False

    with app.app_context():
        user = User.query.filter_by(email=os.environ["ADMIN_EMAIL"]).one()
        user_id = str(user.id)

    with client.session_transaction() as session:
        session["_user_id"] = user_id
        session["_fresh"] = True

    @app.route("/__explode_pending_rollback")
    def _explode_pending_rollback():
        db.session.expire(current_user._get_current_object(), ["email"])
        db.session.add(
            User(
                email=os.environ["ADMIN_EMAIL"],
                password="duplicate-password",
                is_admin=False,
                active=True,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.flush()
        raise RuntimeError("trigger unhandled exception after failed transaction")

    response = client.get("/__explode_pending_rollback")
    body = response.get_data(as_text=True)

    assert response.status_code == 500
    assert "Do not share this publicly" in body
