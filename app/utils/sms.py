import os

from flask import current_app
from twilio.rest import Client


def _value(name: str, default=None):
    env_value = os.getenv(name)
    if env_value is not None:
        return env_value
    config = current_app.config if current_app else {}
    return config.get(name, default)


def send_sms(to_number: str, body: str):
    """Send an SMS message using Twilio credentials from Flask config or env."""
    account_sid = _value("TWILIO_ACCOUNT_SID")
    auth_token = _value("TWILIO_AUTH_TOKEN")
    from_number = _value("TWILIO_PHONE_NUMBER")
    if not (account_sid and auth_token and from_number):
        raise RuntimeError("Twilio settings not configured")
    client = Client(account_sid, auth_token)
    client.messages.create(to=to_number, from_=from_number, body=body)
