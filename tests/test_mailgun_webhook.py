import hashlib
import hmac
import io
import time
from pathlib import Path

from app.models import PosSalesImport


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

    spreadsheet = Path(__file__).resolve().parents[1] / "game_sales.xls"
    content = spreadsheet.read_bytes()

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
    app.config.update({"MAILGUN_WEBHOOK_SIGNING_KEY": "secret-key"})

    response = client.post("/webhooks/mailgun/inbound", data=_payload("secret-key"))

    assert response.status_code == 503
    assert response.get_json()["error"] == "sender_allowlist_not_configured"


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
