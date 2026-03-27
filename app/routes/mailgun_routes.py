"""Webhook routes for Mailgun inbound email processing."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request
from werkzeug.utils import secure_filename

from app.services.pos_sales_ingest import ingest_pos_sales_attachment
from app.utils.activity import log_activity
from app.utils.pos_import_security import (
    DEFAULT_MAX_ATTACHMENT_BYTES,
    csv_config_set,
    normalized_extension_allowlist,
    sender_policy_error,
)

mailgun = Blueprint("mailgun", __name__, url_prefix="/webhooks/mailgun")


def _mailgun_signature_valid() -> bool:
    signing_key = current_app.config.get("MAILGUN_WEBHOOK_SIGNING_KEY") or ""
    if not signing_key:
        return False

    timestamp = (request.form.get("timestamp") or "").strip()
    token = (request.form.get("token") or "").strip()
    signature = (request.form.get("signature") or "").strip().lower()
    if not timestamp or not token or not signature:
        return False

    try:
        request_ts = int(timestamp)
    except (TypeError, ValueError):
        return False

    max_age = int(current_app.config.get("MAILGUN_WEBHOOK_MAX_AGE_SECONDS", 15 * 60))
    now_ts = int(time.time())
    if abs(now_ts - request_ts) > max_age:
        return False

    digest = hmac.new(
        key=signing_key.encode("utf-8"),
        msg=f"{timestamp}{token}".encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(digest, signature)


def _message_id() -> str:
    candidates = (
        request.form.get("Message-Id"),
        request.form.get("message-id"),
        request.form.get("Message-ID"),
    )
    for value in candidates:
        if value and value.strip():
            return value.strip()
    return (
        f"mailgun:{request.form.get('timestamp', '')}:{request.form.get('token', '')}"
    )


@mailgun.route("/inbound", methods=["POST"])
def inbound_mailgun():
    """Receive and stage inbound Mailgun spreadsheet attachments."""

    if not _mailgun_signature_valid():
        return jsonify({"ok": False, "error": "invalid_signature"}), 401

    sender = request.form.get("sender") or request.form.get("from") or ""
    sender_value = sender.strip().lower()

    allowed_senders = csv_config_set(current_app.config.get("MAILGUN_ALLOWED_SENDERS"))
    allowed_domains = csv_config_set(
        current_app.config.get("MAILGUN_ALLOWED_SENDER_DOMAINS")
    )
    sender_error = sender_policy_error(
        sender_value,
        allowed_senders=allowed_senders,
        allowed_domains=allowed_domains,
    )
    if sender_error:
        status_code = 503 if sender_error == "sender_allowlist_not_configured" else 403
        return jsonify({"ok": False, "error": sender_error}), status_code

    normalized_extensions = normalized_extension_allowlist(
        current_app.config.get("MAILGUN_ALLOWED_ATTACHMENT_EXTENSIONS")
    )
    max_attachment_bytes = int(
        current_app.config.get(
            "POS_IMPORT_MAX_ATTACHMENT_BYTES",
            current_app.config.get(
                "MAX_UPLOAD_FILE_SIZE_BYTES", DEFAULT_MAX_ATTACHMENT_BYTES
            ),
        )
    )

    if not request.files:
        return jsonify({"ok": False, "error": "missing_attachment"}), 400

    storage_dir_config = current_app.config.get("MAILGUN_INBOUND_STORAGE_DIR")
    storage_dir = Path(
        storage_dir_config
        or os.path.join(current_app.config["UPLOAD_FOLDER"], "mailgun_inbound")
    )
    storage_dir.mkdir(parents=True, exist_ok=True)

    imported = []
    for upload in request.files.values():
        filename = secure_filename(upload.filename or "")
        if not filename:
            continue

        extension = os.path.splitext(filename)[1].lower()
        if extension not in normalized_extensions:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "unsupported_attachment_type",
                    }
                ),
                400,
            )

        content = upload.read()
        if not content:
            continue
        if len(content) > max_attachment_bytes:
            return jsonify({"ok": False, "error": "attachment_too_large"}), 413

        message_id = _message_id()
        try:
            sales_import, duplicate = ingest_pos_sales_attachment(
                source_provider="mailgun",
                source_message_id=message_id,
                filename=filename,
                content=content,
                storage_dir=storage_dir,
            )
            imported.append({"id": sales_import.id, "duplicate": duplicate})
            if duplicate:
                log_activity(
                    "Received duplicate POS sales import webhook payload "
                    f"for existing import {sales_import.id}"
                )
        except Exception:
            return jsonify({"ok": False, "error": "parse_failed"}), 422

    if not imported:
        return jsonify({"ok": False, "error": "missing_attachment"}), 400

    return jsonify({"ok": True, "imports": imported}), 202
