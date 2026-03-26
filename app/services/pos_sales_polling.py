"""Background mailbox polling for POS sales attachment ingestion."""

from __future__ import annotations

import base64
import email
import imaplib
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email import policy
from pathlib import Path
from threading import Event, Thread

from flask import current_app

from app.services.pos_sales_ingest import ingest_pos_sales_attachment
from app.utils.activity import log_activity

_poller_thread: Thread | None = None
_stop_event = Event()


@dataclass(slots=True)
class PollAttachment:
    filename: str
    content: bytes


@dataclass(slots=True)
class PollMessage:
    message_id: str
    sender: str
    attachments: list[PollAttachment]
    ack_token: str


class MailboxProvider:
    provider_name = "mailbox"

    def fetch_unseen_messages(self) -> list[PollMessage]:
        raise NotImplementedError

    def acknowledge(self, ack_token: str) -> None:
        return


class ImapMailboxProvider(MailboxProvider):
    provider_name = "imap"

    def __init__(self, app):
        self.host = app.config.get("POS_IMPORT_IMAP_HOST", "")
        self.port = int(app.config.get("POS_IMPORT_IMAP_PORT", 993))
        self.username = app.config.get("POS_IMPORT_IMAP_USERNAME", "")
        self.password = app.config.get("POS_IMPORT_IMAP_PASSWORD", "")
        self.mailbox = app.config.get("POS_IMPORT_IMAP_MAILBOX", "INBOX")
        self.use_ssl = bool(app.config.get("POS_IMPORT_IMAP_USE_SSL", True))

    def _client(self):
        if self.use_ssl:
            context = ssl.create_default_context()
            return imaplib.IMAP4_SSL(self.host, self.port, ssl_context=context)
        return imaplib.IMAP4(self.host, self.port)

    def fetch_unseen_messages(self) -> list[PollMessage]:
        if not self.host or not self.username or not self.password:
            raise RuntimeError("IMAP polling requires host, username, and password.")

        messages: list[PollMessage] = []
        with self._client() as client:
            client.login(self.username, self.password)
            client.select(self.mailbox)
            status, data = client.search(None, "UNSEEN")
            if status != "OK" or not data or not data[0]:
                return []

            for uid in data[0].split():
                status, fetched = client.fetch(uid, "(RFC822)")
                if status != "OK" or not fetched:
                    continue

                raw_bytes = None
                for entry in fetched:
                    if isinstance(entry, tuple) and len(entry) >= 2:
                        raw_bytes = entry[1]
                        break
                if not raw_bytes:
                    continue

                parsed = email.message_from_bytes(raw_bytes, policy=policy.default)
                message_id = (
                    parsed.get("Message-ID") or f"imap:{uid.decode()}"
                ).strip()
                sender = (parsed.get("From") or "").strip().lower()
                attachments: list[PollAttachment] = []
                for part in parsed.walk():
                    filename = part.get_filename()
                    if not filename:
                        continue
                    content = part.get_payload(decode=True)
                    if not content:
                        continue
                    attachments.append(
                        PollAttachment(filename=filename, content=content)
                    )

                if attachments:
                    messages.append(
                        PollMessage(
                            message_id=message_id,
                            sender=sender,
                            attachments=attachments,
                            ack_token=uid.decode(),
                        )
                    )

            return messages

    def acknowledge(self, ack_token: str) -> None:
        if not ack_token:
            return
        with self._client() as client:
            client.login(self.username, self.password)
            client.select(self.mailbox)
            client.store(ack_token, "+FLAGS", "\\Seen")


class ApiMailboxProvider(MailboxProvider):
    provider_name = "api"

    def __init__(self, app):
        self.base_url = (app.config.get("POS_IMPORT_API_BASE_URL") or "").rstrip("/")
        self.messages_path = app.config.get(
            "POS_IMPORT_API_MESSAGES_PATH", "/messages/unseen"
        )
        self.ack_path_template = app.config.get(
            "POS_IMPORT_API_ACK_PATH_TEMPLATE", "/messages/{message_id}/ack"
        )
        self.token = app.config.get("POS_IMPORT_API_TOKEN", "")

    def _request(self, method: str, path: str, payload: dict | None = None):
        if not self.base_url:
            raise RuntimeError("API polling requires POS_IMPORT_API_BASE_URL.")
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    def fetch_unseen_messages(self) -> list[PollMessage]:
        payload = self._request("GET", self.messages_path)
        message_entries = (
            payload.get("messages") if isinstance(payload, dict) else payload
        )
        if not isinstance(message_entries, list):
            return []

        messages: list[PollMessage] = []
        for entry in message_entries:
            if not isinstance(entry, dict):
                continue
            message_id = str(entry.get("id") or "").strip()
            if not message_id:
                continue
            attachments: list[PollAttachment] = []
            for attachment in entry.get("attachments") or []:
                if not isinstance(attachment, dict):
                    continue
                filename = str(attachment.get("filename") or "").strip()
                encoded = attachment.get("content_base64")
                if not filename or not isinstance(encoded, str):
                    continue
                try:
                    content = base64.b64decode(encoded)
                except (ValueError, TypeError):
                    continue
                if not content:
                    continue
                attachments.append(PollAttachment(filename=filename, content=content))

            if attachments:
                messages.append(
                    PollMessage(
                        message_id=message_id,
                        sender=str(entry.get("sender") or "").strip().lower(),
                        attachments=attachments,
                        ack_token=message_id,
                    )
                )
        return messages

    def acknowledge(self, ack_token: str) -> None:
        if not ack_token:
            return
        path = self.ack_path_template.format(message_id=ack_token)
        try:
            self._request("POST", path, payload={"status": "processed"})
        except urllib.error.URLError:
            current_app.logger.warning(
                "Failed to acknowledge polled API message %s", ack_token
            )


def _csv_config_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {entry.strip().lower() for entry in value.split(",") if entry.strip()}


def _ingest_mode_enabled(app) -> bool:
    mode = (app.config.get("POS_IMPORT_INGEST_MODE") or "webhook").strip().lower()
    return mode == "poll"


def _build_provider(app) -> MailboxProvider:
    provider = (app.config.get("POS_IMPORT_POLL_PROVIDER") or "imap").strip().lower()
    if provider == "imap":
        return ImapMailboxProvider(app)
    if provider == "api":
        return ApiMailboxProvider(app)
    raise RuntimeError(f"Unsupported POS_IMPORT_POLL_PROVIDER: {provider}")


def _attachment_allowed(filename: str, allowed_extensions: set[str]) -> bool:
    extension = Path(filename).suffix.lower()
    return bool(extension and extension in allowed_extensions)


def run_pos_sales_mailbox_poll_once(app) -> dict[str, int]:
    """Run a single polling pass and ingest supported attachments."""

    if hasattr(app, "_get_current_object"):
        app = app._get_current_object()

    with app.app_context():
        if not _ingest_mode_enabled(app):
            return {"messages": 0, "imports": 0, "duplicates": 0, "errors": 0}

        provider = _build_provider(app)
        allowed = _csv_config_set(
            app.config.get("MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS", "xls,xlsx")
        )
        allowed_extensions = {
            ext if ext.startswith(".") else f".{ext}" for ext in allowed
        }
        storage_root = Path(
            app.config.get("MAILGUN_INBOUND_STORAGE_DIR")
            or os.path.join(app.config["UPLOAD_FOLDER"], "mailgun_inbound")
        )

        result = {"messages": 0, "imports": 0, "duplicates": 0, "errors": 0}
        for message in provider.fetch_unseen_messages():
            result["messages"] += 1
            message_failed = False
            for attachment in message.attachments:
                if not _attachment_allowed(attachment.filename, allowed_extensions):
                    continue
                try:
                    _, duplicate = ingest_pos_sales_attachment(
                        source_provider=f"poll:{provider.provider_name}",
                        source_message_id=message.message_id,
                        filename=attachment.filename,
                        content=attachment.content,
                        storage_dir=storage_root,
                    )
                    if duplicate:
                        result["duplicates"] += 1
                    else:
                        result["imports"] += 1
                except Exception:
                    result["errors"] += 1
                    message_failed = True

            if not message_failed:
                provider.acknowledge(message.ack_token)

        if result["imports"] or result["duplicates"] or result["errors"]:
            log_activity(
                "Completed POS import polling pass "
                f"(provider={provider.provider_name}, imports={result['imports']}, "
                f"duplicates={result['duplicates']}, errors={result['errors']})."
            )

        return result


def _poll_loop(app, interval_seconds: int) -> None:
    next_run = time.monotonic()
    while True:
        remaining = next_run - time.monotonic()
        if remaining > 0 and _stop_event.wait(remaining):
            break
        if _stop_event.is_set():
            break

        try:
            run_pos_sales_mailbox_poll_once(app)
        except Exception:
            with app.app_context():
                current_app.logger.exception("POS import mailbox poller run failed")

        next_run += interval_seconds
        while next_run <= time.monotonic():
            next_run += interval_seconds


def start_pos_sales_mailbox_poller(app) -> None:
    """Start or restart the POS mailbox poller thread when enabled."""

    global _poller_thread, _stop_event

    if hasattr(app, "_get_current_object"):
        app = app._get_current_object()

    if _poller_thread and _poller_thread.is_alive():
        _stop_event.set()
        _poller_thread.join()
        _stop_event = Event()

    if not _ingest_mode_enabled(app):
        return

    # Avoid duplicate threads from the debug reloader parent process.
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    interval_seconds = int(app.config.get("POS_IMPORT_POLL_INTERVAL_SECONDS", 3600))
    interval_seconds = max(60, interval_seconds)
    _poller_thread = Thread(
        target=_poll_loop,
        args=(app, interval_seconds),
        daemon=True,
        name="pos-import-mailbox-poller",
    )
    _poller_thread.start()


__all__ = ["run_pos_sales_mailbox_poll_once", "start_pos_sales_mailbox_poller"]
