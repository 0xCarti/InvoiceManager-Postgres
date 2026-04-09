import pytest

from app.utils import email as email_utils


class DummySMTP:
    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.login_args = None
        self.sent_message = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self):
        self.started_tls = True

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message):
        self.sent_message = message


def test_send_email_uses_app_config_over_env(monkeypatch, app):
    # Remove environment overrides so config values are used
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_SENDER",
        "SMTP_USE_TLS",
        "SMTP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    with app.app_context():
        app.config.update(
            {
                "SMTP_HOST": "config-host",
                "SMTP_PORT": 2525,
                "SMTP_USERNAME": "config-user",
                "SMTP_PASSWORD": "config-pass",
                "SMTP_SENDER": "sender@example.com",
                "SMTP_USE_TLS": True,
                "SMTP_TIMEOUT_SECONDS": 12,
            }
        )

        dummy = DummySMTP("", 0)

        def fake_smtp(host, port, timeout=None):
            dummy.host = host
            dummy.port = port
            dummy.timeout = timeout
            return dummy

        monkeypatch.setattr(email_utils.smtplib, "SMTP", fake_smtp)

        email_utils.send_email(
            to_address="dest@example.com",
            subject="Subject",
            body="Body",
        )

        assert dummy.host == "config-host"
        assert dummy.port == 2525
        assert dummy.timeout == 12.0
        assert dummy.started_tls is True
        assert dummy.login_args == ("config-user", "config-pass")
        assert dummy.sent_message["From"] == "sender@example.com"
        assert dummy.sent_message["To"] == "dest@example.com"


def test_send_email_raises_configuration_error_when_missing_settings(monkeypatch, app):
    # Ensure no environment values are present
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_SENDER",
        "SMTP_USE_TLS",
        "SMTP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    with app.app_context():
        app.config.update({"SMTP_USERNAME": "user"})
        with pytest.raises(email_utils.SMTPConfigurationError) as excinfo:
            email_utils.send_email(
                to_address="dest@example.com",
                subject="Subject",
                body="Body",
            )

    assert "SMTP_HOST" in str(excinfo.value)
    assert excinfo.value.missing_settings == ["SMTP_HOST"]


def test_send_email_raises_configuration_error_when_timeout_is_invalid(monkeypatch, app):
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_SENDER",
        "SMTP_USE_TLS",
        "SMTP_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(key, raising=False)

    with app.app_context():
        app.config.update(
            {
                "SMTP_HOST": "config-host",
                "SMTP_PORT": 2525,
                "SMTP_SENDER": "sender@example.com",
                "SMTP_TIMEOUT_SECONDS": "bad-timeout",
            }
        )
        with pytest.raises(email_utils.SMTPConfigurationError) as excinfo:
            email_utils.send_email(
                to_address="dest@example.com",
                subject="Subject",
                body="Body",
            )

    assert excinfo.value.missing_settings == ["SMTP_TIMEOUT_SECONDS"]
