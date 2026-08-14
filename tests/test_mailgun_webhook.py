import hashlib
import hmac
import io
import time
from pathlib import Path
from types import SimpleNamespace

from app.models import PosSalesImport
from app.services.pos_sales_ingest import PosSalesImportStorageError
from tests.utils import build_terminal_sales_workbook_bytes


def _signature(signing_key: str, timestamp: str, token: str) -> str:
    return hmac.new(
        signing_key.encode("utf-8"),
        f"{timestamp}{token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _payload(signing_key: str, *, sender: str = "reports@example.com") -> dict:
    timestamp = str(int(time.time()))
    token = "tok-123"
    return {
        "timestamp": timestamp,
        "token": token,
        "signature": _signature(signing_key, timestamp, token),
        "sender": sender,
        "Message-Id": "<mailgun-test-message-id>",
    }


def test_mailgun_webhook_rejects_invalid_signature(client, app):
    app.config.update({"MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key"})
    response = client.post(
        "/webhooks/mailgun/inbound",
        data={"timestamp": "1", "token": "abc", "signature": "bad"},
    )
    assert response.status_code == 401


def test_mailgun_webhook_rejects_non_spreadsheet_attachment(client, app):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
            "MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS": "xls,xlsx",
        }
    )
    data = _payload("secret-key")
    data["attachment-1"] = (io.BytesIO(b"not excel"), "notes.txt")

    response = client.post(
        "/webhooks/mailgun/inbound", data=data, content_type="multipart/form-data"
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "unsupported_attachment_type"
    assert "filename" not in payload


def test_mailgun_webhook_stages_import_and_deduplicates(client, app, tmp_path):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
            "MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS": "xls,xlsx",
            "MAILGUN_INBOUND_STORAGE_DIR": str(tmp_path / "mailgun_staging"),
        }
    )

    content = build_terminal_sales_workbook_bytes()

    request_data = _payload("secret-key")
    request_data["attachment-1"] = (io.BytesIO(content), "game_sales.xls")

    first = client.post(
        "/webhooks/mailgun/inbound",
        data=request_data,
        content_type="multipart/form-data",
    )
    assert first.status_code == 202
    first_json = first.get_json()
    assert first_json["ok"] is True
    assert first_json["imports"][0]["duplicate"] is False

    with app.app_context():
        created = PosSalesImport.query.one()
        assert created.status == "pending"
        assert created.attachment_storage_path
        assert Path(created.attachment_storage_path).exists()
        assert len(created.locations) > 0
        assert len(created.rows) > 0

    second_data = _payload("secret-key")
    second_data["Message-Id"] = "<mailgun-test-message-id>"
    second_data["attachment-1"] = (io.BytesIO(content), "game_sales.xls")

    second = client.post(
        "/webhooks/mailgun/inbound",
        data=second_data,
        content_type="multipart/form-data",
    )
    assert second.status_code == 202
    second_json = second.get_json()
    assert second_json["imports"][0]["duplicate"] is True

    with app.app_context():
        assert PosSalesImport.query.count() == 1


def test_mailgun_webhook_rejects_stale_timestamp(client, app):
    app.config.update({"MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key"})
    stale_timestamp = str(int(time.time()) - 3600)
    token = "tok-123"

    response = client.post(
        "/webhooks/mailgun/inbound",
        data={
            "timestamp": stale_timestamp,
            "token": token,
            "signature": _signature("secret-key", stale_timestamp, token),
        },
    )
    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid_signature"


def test_mailgun_webhook_rejects_missing_attachment_payload(client, app):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
        }
    )

    response = client.post("/webhooks/mailgun/inbound", data=_payload("secret-key"))
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["ok"] is False
    assert payload["error"] == "missing_attachment"


def test_mailgun_webhook_requires_sender_allowlist(client, app):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDERS": "",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "",
        }
    )

    response = client.post("/webhooks/mailgun/inbound", data=_payload("secret-key"))

    assert response.status_code == 503
    assert response.get_json()["error"] == "sender_allowlist_not_configured"


def test_mailgun_webhook_allows_exact_sender_or_domain_match(client, app):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDERS": "keystonecentrereports@gmail.com",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "mail.brycecotton.ca,brycecotton.ca",
        }
    )

    exact_sender = client.post(
        "/webhooks/mailgun/inbound",
        data=_payload("secret-key", sender="keystonecentrereports@gmail.com"),
    )
    assert exact_sender.status_code == 400
    assert exact_sender.get_json()["error"] == "missing_attachment"

    domain_sender = client.post(
        "/webhooks/mailgun/inbound",
        data=_payload("secret-key", sender="reports@mail.brycecotton.ca"),
    )
    assert domain_sender.status_code == 400
    assert domain_sender.get_json()["error"] == "missing_attachment"

    rejected = client.post(
        "/webhooks/mailgun/inbound",
        data=_payload("secret-key", sender="reports@example.com"),
    )
    assert rejected.status_code == 403
    assert rejected.get_json()["error"] == "sender_not_allowed"


def test_mailgun_webhook_rejects_oversized_attachment(client, app):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
            "MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS": "xls,xlsx",
            "POS_IMPORT_MAX_ATTACHMENT_BYTES": 4,
        }
    )
    data = _payload("secret-key")
    data["attachment-1"] = (io.BytesIO(b"12345"), "game_sales.xls")

    response = client.post(
        "/webhooks/mailgun/inbound", data=data, content_type="multipart/form-data"
    )

    assert response.status_code == 413
    assert response.get_json()["error"] == "attachment_too_large"


def test_mailgun_webhook_ignores_empty_sales_attachment(client, app, monkeypatch):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
            "MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS": "xls,xlsx",
        }
    )
    monkeypatch.setattr(
        "app.routes.mailgun_routes.ingest_pos_sales_attachment",
        lambda **kwargs: (
            SimpleNamespace(id=19, status=PosSalesImport.STATUS_IGNORED),
            False,
        ),
    )

    data = _payload("secret-key")
    data["attachment-1"] = (io.BytesIO(b"empty report"), "empty_sales.xls")

    response = client.post(
        "/webhooks/mailgun/inbound", data=data, content_type="multipart/form-data"
    )

    assert response.status_code == 202
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["imports"] == []
    assert payload["ignored"] == [
        {"filename": "empty_sales.xls", "reason": "empty_sales_file", "id": 19}
    ]

    with app.app_context():
        assert PosSalesImport.query.count() == 0


def test_mailgun_webhook_returns_retryable_error_for_unavailable_storage(
    client, app, monkeypatch, caplog
):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
            "MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS": "xls,xlsx",
        }
    )

    def _raise_storage_error(**kwargs):
        raise PosSalesImportStorageError("simulated storage failure")

    monkeypatch.setattr(
        "app.routes.mailgun_routes.ingest_pos_sales_attachment",
        _raise_storage_error,
    )
    data = _payload("secret-key", sender="private-sender@example.com")
    data["attachment-1"] = (
        io.BytesIO(b"spreadsheet bytes"),
        "../../daily sales.xls",
    )

    logger_was_disabled = app.logger.disabled
    app.logger.disabled = False
    app.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("ERROR", logger=app.logger.name):
            response = client.post(
                "/webhooks/mailgun/inbound",
                data=data,
                content_type="multipart/form-data",
            )
    finally:
        app.logger.removeHandler(caplog.handler)
        app.logger.disabled = logger_was_disabled

    assert response.status_code == 503
    assert response.get_json() == {"ok": False, "error": "storage_unavailable"}
    assert "Mailgun POS sales attachment storage is unavailable" in caplog.text
    assert "daily_sales.xls" in caplog.text
    assert "secret-key" not in caplog.text
    assert "tok-123" not in caplog.text
    assert "private-sender@example.com" not in caplog.text
    assert "spreadsheet bytes" not in caplog.text


def test_mailgun_webhook_logs_parse_failures(client, app, monkeypatch, caplog):
    app.config.update(
        {
            "MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key",
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
            "MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS": "xls,xlsx",
        }
    )

    def _raise_parse_error(**kwargs):
        raise ValueError("simulated parser failure")

    monkeypatch.setattr(
        "app.routes.mailgun_routes.ingest_pos_sales_attachment",
        _raise_parse_error,
    )
    data = _payload("secret-key")
    data["attachment-1"] = (io.BytesIO(b"invalid workbook"), "daily_sales.xls")

    logger_was_disabled = app.logger.disabled
    app.logger.disabled = False
    app.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level("ERROR", logger=app.logger.name):
            response = client.post(
                "/webhooks/mailgun/inbound",
                data=data,
                content_type="multipart/form-data",
            )
    finally:
        app.logger.removeHandler(caplog.handler)
        app.logger.disabled = logger_was_disabled

    assert response.status_code == 422
    assert response.get_json() == {"ok": False, "error": "parse_failed"}
    assert (
        "Failed to ingest Mailgun POS sales attachment daily_sales.xls"
        in caplog.text
    )
