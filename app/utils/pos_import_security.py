"""Shared security helpers for POS attachment ingestion."""

from __future__ import annotations

from email.utils import parseaddr
from pathlib import Path

DEFAULT_ATTACHMENT_EXTENSIONS = "xls,xlsx"
DEFAULT_MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024


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
