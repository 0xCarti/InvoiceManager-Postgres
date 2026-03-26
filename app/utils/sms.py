import os

from twilio.rest import Client


def send_sms(to_number: str, body: str):
    """Send an SMS message using Twilio credentials from environment variables."""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    from_number = os.getenv("TWILIO_PHONE_NUMBER")
    if not (account_sid and auth_token and from_number):
        raise RuntimeError("Twilio settings not configured")
    client = Client(account_sid, auth_token)
    client.messages.create(to=to_number, from_=from_number, body=body)
