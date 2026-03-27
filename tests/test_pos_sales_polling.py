from __future__ import annotations

from pathlib import Path

from app.models import PosSalesImport
from app.services import pos_sales_polling
from app.services.pos_sales_polling import PollAttachment, PollMessage


class _StubProvider:
    provider_name = "imap"

    def __init__(self, messages):
        self._messages = messages
        self.ack_tokens: list[str] = []

    def fetch_unseen_messages(self):
        return list(self._messages)

    def acknowledge(self, ack_token: str) -> None:
        self.ack_tokens.append(ack_token)


def test_poll_once_ingests_and_deduplicates(app, monkeypatch, tmp_path):
    spreadsheet = Path(__file__).resolve().parents[1] / "game_sales.xls"
    content = spreadsheet.read_bytes()

    message = PollMessage(
        message_id="<poll-test-message>",
        sender="reports@example.com",
        ack_token="uid-1",
        attachments=[PollAttachment(filename="game_sales.xls", content=content)],
    )
    provider = _StubProvider([message])

    app.config.update(
        {
            "POS_IMPORT_INGEST_MODE": "poll",
            "MAILGUN_INBOUND_STORAGE_DIR": str(tmp_path / "mailgun_staging"),
            "MAILGUN_ALLOWED_SENDER_DOMAINS": "example.com",
        }
    )

    monkeypatch.setattr(pos_sales_polling, "_build_provider", lambda _app: provider)

    first = pos_sales_polling.run_pos_sales_mailbox_poll_once(app)
    assert first == {"messages": 1, "imports": 1, "duplicates": 0, "errors": 0}

    second = pos_sales_polling.run_pos_sales_mailbox_poll_once(app)
    assert second == {"messages": 1, "imports": 0, "duplicates": 1, "errors": 0}

    with app.app_context():
        created = PosSalesImport.query.one()
        assert created.source_provider == "poll:imap"
        assert created.message_id == "<poll-test-message>"
        assert created.status == "pending"
        assert created.attachment_storage_path
        assert Path(created.attachment_storage_path).exists()

    assert provider.ack_tokens == ["uid-1", "uid-1"]


def test_poll_once_noop_when_mode_not_poll(app):
    app.config.update({"POS_IMPORT_INGEST_MODE": "webhook"})

    result = pos_sales_polling.run_pos_sales_mailbox_poll_once(app)

    assert result == {"messages": 0, "imports": 0, "duplicates": 0, "errors": 0}


def test_poll_once_rejects_messages_when_sender_allowlist_missing(app, monkeypatch):
    message = PollMessage(
        message_id="<poll-test-missing-allowlist>",
        sender="reports@example.com",
        ack_token="uid-2",
        attachments=[PollAttachment(filename="game_sales.xls", content=b"1234")],
    )
    provider = _StubProvider([message])

    app.config.update({"POS_IMPORT_INGEST_MODE": "poll"})
    monkeypatch.setattr(pos_sales_polling, "_build_provider", lambda _app: provider)

    result = pos_sales_polling.run_pos_sales_mailbox_poll_once(app)

    assert result == {"messages": 1, "imports": 0, "duplicates": 0, "errors": 1}
    assert provider.ack_tokens == []
