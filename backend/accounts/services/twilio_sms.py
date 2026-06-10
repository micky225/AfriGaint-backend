import logging
import os

logger = logging.getLogger(__name__)


def send_sms(to_phone: str, message: str) -> bool:
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_PHONE_NUMBER", "")

    if not all([account_sid, auth_token, from_number]):
        logger.warning("Twilio not configured. SMS to %s: %s", to_phone, message)
        return True

    try:
        from twilio.rest import Client

        client = Client(account_sid, auth_token)
        client.messages.create(body=message, from_=from_number, to=to_phone)
        return True
    except Exception:
        logger.exception("Failed to send SMS to %s", to_phone)
        return False
