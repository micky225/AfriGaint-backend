import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MOOLRE_CHANNEL_MTN = "13"
MOOLRE_CHANNEL_TELECEL = "6"
MOOLRE_CHANNEL_AT = "7"

NETWORK_TO_CHANNEL = {
    "mtn": MOOLRE_CHANNEL_MTN,
    "telecel": MOOLRE_CHANNEL_TELECEL,
    "vodafone": MOOLRE_CHANNEL_TELECEL,
    "at": MOOLRE_CHANNEL_AT,
    "airteltigo": MOOLRE_CHANNEL_AT,
}


class MoolreError(Exception):
    def __init__(self, message: str, code: str = "moolre_error", *, response: dict | None = None):
        self.message = message
        self.code = code
        self.response = response or {}
        super().__init__(message)


class MoolreNotConfigured(MoolreError):
    def __init__(self):
        super().__init__(
            "Payment provider is not configured. Contact support.",
            code="payment_not_configured",
        )


def moolre_payer_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    if digits.startswith("233") and len(digits) >= 12:
        return f"0{digits[3:]}"
    return digits


def resolve_moolre_channel(network: str = "") -> str:
    normalized = (network or "mtn").strip().lower()
    return NETWORK_TO_CHANNEL.get(normalized, MOOLRE_CHANNEL_MTN)


def _moolre_headers() -> dict[str, str]:
    return {
        "X-API-USER": settings.MOOLRE_USER,
        "X-API-PUBKEY": settings.MOOLRE_PUB_KEY,
    }


def _moolre_base_url() -> str:
    if settings.MOOLRE_SANDBOX:
        return "https://sandbox.moolre.com"
    return "https://api.moolre.com"


def _ensure_configured():
    if not settings.MOOLRE_USER or not settings.MOOLRE_PUB_KEY or not settings.MOOLRE_ACCOUNT_ID:
        raise MoolreNotConfigured()


def initiate_checkout(
    customer_phone: str,
    amount,
    *,
    externalref: str,
    channel: str = MOOLRE_CHANNEL_MTN,
    otp_code: str = "",
    session_id: str = "",
) -> dict:
    _ensure_configured()

    payload = {
        "type": 1,
        "channel": channel,
        "currency": "GHS",
        "amount": str(amount),
        "payer": moolre_payer_phone(customer_phone),
        "externalref": externalref,
        "accountnumber": settings.MOOLRE_ACCOUNT_ID,
    }
    if otp_code:
        payload["otpcode"] = otp_code
    if session_id:
        payload["sessionid"] = session_id

    url = f"{_moolre_base_url()}/open/transact/payment"
    try:
        response = requests.post(url, json=payload, headers=_moolre_headers(), timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Moolre initiate payment request failed")
        raise MoolreError("Unable to reach payment provider. Try again.", code="provider_unreachable") from exc

    data = response.json()
    code = str(data.get("code", ""))

    if code == "TP14":
        return {
            "payment_status": "otp_required",
            "message": data.get("message") or "Complete SMS verification and submit the OTP.",
            "provider_response": data,
        }
    if code == "TR099":
        return {
            "payment_status": "pending",
            "message": "Approve the payment prompt on your phone.",
            "provider_reference": data.get("data") or "",
            "provider_response": data,
        }
    if str(data.get("status")) in {"0", "false"}:
        raise MoolreError(
            data.get("message") or "Payment could not be started.",
            code=code or "payment_failed",
            response=data,
        )

    return {
        "payment_status": "pending",
        "message": data.get("message") or "Payment initiated.",
        "provider_reference": data.get("data") or "",
        "provider_response": data,
    }


def check_payment_status(externalref: str) -> dict:
    _ensure_configured()

    payload = {
        "type": 1,
        "idtype": "1",
        "id": externalref,
        "accountnumber": settings.MOOLRE_ACCOUNT_ID,
    }
    url = f"{_moolre_base_url()}/open/transact/status"
    try:
        response = requests.post(url, json=payload, headers=_moolre_headers(), timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Moolre payment status request failed")
        raise MoolreError("Unable to verify payment status.", code="provider_unreachable") from exc

    return response.json()


def is_successful_status_response(data: dict) -> bool:
    if str(data.get("status")) in {"1", "true"} and str(data.get("code", "")).upper() in {"SS01", "P01"}:
        return True
    tx = data.get("data") or {}
    if isinstance(tx, dict) and str(tx.get("txstatus")) == "1":
        return True
    return False
