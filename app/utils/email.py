import os
import smtplib
from email.message import EmailMessage
from typing import Optional, Sequence, Tuple

from flask import current_app


Attachment = Tuple[str, bytes, str]


class SMTPConfigurationError(RuntimeError):
    """Raised when the SMTP configuration is incomplete."""

    def __init__(self, missing_settings: Sequence[str]):
        message = "Missing SMTP settings: " + ", ".join(missing_settings)
        super().__init__(message)
        self.missing_settings = list(missing_settings)


def _get_smtp_config():
    config = current_app.config if current_app else {}

    def _value(name: str, default=None):
        env_value = os.getenv(name)
        if env_value is not None:
            return env_value
        return config.get(name, default)

    host = (_value("SMTP_HOST") or "").strip()
    port_raw = _value("SMTP_PORT", 25)
    username = (_value("SMTP_USERNAME") or "").strip()
    password = _value("SMTP_PASSWORD") or ""
    sender = (_value("SMTP_SENDER") or "").strip()
    use_tls_raw = _value("SMTP_USE_TLS", False)

    missing = []
    if not host:
        missing.append("SMTP_HOST")
    if not sender and not username:
        missing.append("SMTP_SENDER")

    if missing:
        raise SMTPConfigurationError(missing)

    try:
        port = int(str(port_raw))
    except (TypeError, ValueError):
        raise SMTPConfigurationError(["SMTP_PORT"])

    if isinstance(use_tls_raw, bool):
        use_tls = use_tls_raw
    else:
        use_tls = str(use_tls_raw).lower() in ("1", "true", "yes", "on")

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "from_address": sender or username,
        "use_tls": use_tls,
    }


def send_email(
    to_address: str,
    subject: str,
    body: str,
    attachments: Optional[Sequence[Attachment]] = None,
):
    """Send an email using SMTP settings from Flask configuration."""
    smtp_config = _get_smtp_config()

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_config["from_address"]
    msg["To"] = to_address
    msg.set_content(body)

    if attachments:
        for filename, content, mimetype in attachments:
            maintype, _, subtype = mimetype.partition("/")
            if not subtype:
                maintype, subtype = "application", "octet-stream"
            if isinstance(content, str):
                content = content.encode("utf-8")
            msg.add_attachment(
                content,
                maintype=maintype,
                subtype=subtype,
                filename=filename,
            )

    with smtplib.SMTP(smtp_config["host"], smtp_config["port"]) as server:
        if smtp_config["use_tls"]:
            server.starttls()
        if smtp_config["username"]:
            server.login(smtp_config["username"], smtp_config["password"])
        server.send_message(msg)
