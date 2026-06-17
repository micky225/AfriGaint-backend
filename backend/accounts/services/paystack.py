import hashlib
import hmac
import logging
from decimal import Decimal

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PAYSTACK_API_BASE = "https://api.paystack.co"


class PaystackError(Exception):
    def __init__(self, message: str, code: str = "paystack_error", *, response: dict | None = None):
        self.message = message
        self.code = code
        self.response = response or {}
        super().__init__(message)


class PaystackNotConfigured(PaystackError):
    def __init__(self):
        super().__init__(
            "Payment provider is not configured. Contact support.",
            code="payment_not_configured",
        )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _ensure_configured():
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackNotConfigured()


def _request_timeout() -> float:
    return max(3.0, float(getattr(settings, "PAYSTACK_REQUEST_TIMEOUT_SECONDS", 30)))


def _status_timeout() -> float:
    return max(3.0, float(getattr(settings, "PAYSTACK_STATUS_TIMEOUT_SECONDS", 10)))


def amount_to_subunit(amount) -> int:
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1")))


def initialize_transaction(
    *,
    email: str,
    amount,
    reference: str,
    metadata: dict | None = None,
) -> dict:
    """Start a Paystack checkout and return the hosted payment URL."""
    _ensure_configured()

    payload = {
        "email": email,
        "amount": amount_to_subunit(amount),
        "currency": settings.PAYSTACK_CURRENCY,
        "reference": reference,
        "callback_url": settings.PAYSTACK_CALLBACK_URL,
        "metadata": metadata or {},
    }

    url = f"{PAYSTACK_API_BASE}/transaction/initialize"
    try:
        response = requests.post(url, json=payload, headers=_headers(), timeout=_request_timeout())
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Paystack initialize request failed for %s", reference)
        raise PaystackError("Unable to reach payment provider. Try again.", code="provider_unreachable") from exc

    body = response.json()
    logger.info("Paystack initialize response for %s: %s", reference, body)

    if not body.get("status"):
        raise PaystackError(
            body.get("message") or "Payment could not be started.",
            code="payment_failed",
            response=body,
        )

    data = body.get("data") or {}
    payment_url = str(data.get("authorization_url") or "").strip()
    if not payment_url:
        raise PaystackError(
            "Payment page could not be created.",
            code="payment_url_missing",
            response=body,
        )

    return {
        "payment_status": "pending",
        "message": "Complete payment on the Paystack checkout page.",
        "payment_url": payment_url,
        "access_code": str(data.get("access_code") or ""),
        "provider_reference": str(data.get("reference") or reference),
        "provider_response": body,
    }


def verify_transaction(reference: str) -> dict:
    _ensure_configured()

    url = f"{PAYSTACK_API_BASE}/transaction/verify/{reference}"
    try:
        response = requests.get(url, headers=_headers(), timeout=_status_timeout())
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Paystack verify request failed for %s", reference)
        raise PaystackError("Unable to verify payment status.", code="provider_unreachable") from exc

    return response.json()


def is_successful_verify_response(data: dict) -> bool:
    if not data.get("status"):
        return False
    tx = data.get("data") or {}
    return str(tx.get("status", "")).lower() == "success"


def verify_webhook_signature(payload_body: bytes, signature: str) -> bool:
    secret = settings.PAYSTACK_SECRET_KEY
    if not secret or not signature:
        return False
    digest = hmac.new(secret.encode("utf-8"), payload_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(digest, signature)


def extract_reference_from_webhook(payload: dict) -> str:
    data = payload.get("data") or {}
    return str(data.get("reference") or "").strip()


def extract_provider_reference_from_webhook(payload: dict) -> str:
    data = payload.get("data") or {}
    return str(data.get("id") or data.get("transaction_id") or "").strip()


def is_charge_success_webhook(payload: dict) -> bool:
    if payload.get("event") != "charge.success":
        return False
    data = payload.get("data") or {}
    return str(data.get("status", "")).lower() == "success"
