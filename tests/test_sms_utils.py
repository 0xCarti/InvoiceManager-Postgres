from types import SimpleNamespace

import pytest


def test_send_sms_missing_settings(monkeypatch):
    # Ensure environment variables are absent
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("TWILIO_PHONE_NUMBER", raising=False)
    from app.utils import sms

    with pytest.raises(RuntimeError):
        sms.send_sms("+123", "hi")


def test_send_sms_success(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "sid")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+1999")

    calls = []

    class DummyClient:
        def __init__(self, sid, token):
            calls.append(("init", sid, token))
            self.messages = self

        def create(self, to, from_, body):
            calls.append(("send", to, from_, body))

    monkeypatch.setattr("app.utils.sms.Client", DummyClient)
    from app.utils import sms

    sms.send_sms("+1555", "Hello")
    assert calls == [
        ("init", "sid", "token"),
        ("send", "+1555", "+1999", "Hello"),
    ]
