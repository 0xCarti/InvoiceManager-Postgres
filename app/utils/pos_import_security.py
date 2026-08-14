"""Shared security helpers for POS attachment ingestion."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from email.utils import parseaddr
from pathlib import Path
from typing import Any

DEFAULT_ATTACHMENT_EXTENSIONS = "xls,xlsx"
DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


def resolve_pos_import_storage_dir(config: Mapping[str, Any]) -> Path:
    """Return the configured directory for persisted POS import attachments."""

    configured_dir = str(config.get("MAILGUN_INBOUND_STORAGE_DIR") or "").strip()
    if configured_dir:
        return Path(configured_dir)

    upload_folder = config.get("UPLOAD_FOLDER")
    if not upload_folder:
        raise RuntimeError(
            "UPLOAD_FOLDER is required to resolve the POS sales import storage "
            "directory."
        )
    return Path(str(upload_folder)) / "mailgun_inbound"


def ensure_pos_import_storage_writable(config: Mapping[str, Any]) -> Path:
    """Create and write-test the POS import attachment directory.

    A real create/write/delete probe catches bind-mount ownership and ACL issues
    that existence checks and ``os.access`` can miss.
    """

    storage_dir = resolve_pos_import_storage_dir(config)
    probe_path: Path | None = None
    try:
        storage_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=".pos-import-write-probe-",
            dir=storage_dir,
            delete=False,
        ) as probe:
            probe_path = Path(probe.name)
            probe.write(b"probe")
            probe.flush()
            os.fsync(probe.fileno())
        probe_path.unlink()
        probe_path = None
    except OSError as exc:
        raise RuntimeError(
            "POS sales import storage directory is not writable: "
            f"{storage_dir}. Ensure the application user can create and delete "
            "files there."
        ) from exc
    finally:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass

    return storage_dir


def csv_config_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {entry.strip().lower() for entry in value.split(",") if entry.strip()}


def extract_email_address(email_value: str | None) -> str:
    if not email_value:
        return ""
    _, parsed = parseaddr(email_value)
    candidate = parsed or email_value
    return candidate.strip().lower()


def extract_email_domain(email_value: str | None) -> str:
    candidate = extract_email_address(email_value)
    if "@" not in candidate:
        return ""
    return candidate.split("@", 1)[1].strip().lower()


def sender_policy_error(
    sender_value: str | None,
    *,
    allowed_senders: set[str],
    allowed_domains: set[str],
) -> str | None:
    normalized_sender = extract_email_address(sender_value)
    if not allowed_senders and not allowed_domains:
        return "sender_allowlist_not_configured"
    sender_domain = extract_email_domain(normalized_sender)
    sender_allowed = bool(allowed_senders) and normalized_sender in allowed_senders
    domain_allowed = bool(allowed_domains) and sender_domain in allowed_domains

    if allowed_senders and allowed_domains:
        if sender_allowed or domain_allowed:
            return None
        return "sender_not_allowed"
    if allowed_senders and not sender_allowed:
        return "sender_not_allowed"
    if allowed_domains and not domain_allowed:
        return "sender_domain_not_allowed"
    return None


def normalized_extension_allowlist(
    raw_value: str | None,
    *,
    default: str = DEFAULT_ATTACHMENT_EXTENSIONS,
) -> set[str]:
    entries = csv_config_set(raw_value or default)
    return {entry if entry.startswith(".") else f".{entry}" for entry in entries}


def attachment_allowed(filename: str, allowed_extensions: set[str]) -> bool:
    extension = Path(filename).suffix.lower()
    return bool(extension and extension in allowed_extensions)
