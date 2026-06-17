"""
Moolre payment integration (disabled).

Kept for reference only. GHS deposits now use Paystack in paystack.py.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

MOOLRE_SUCCESS_CODES = frozenset({"SS01", "P01", "POS01", "POS10", "TR099"})


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


def _moolre_headers() -> dict[str, str]:
    return {
        "X-API-USER": settings.MOOLRE_USER,
        "X-API-PUBKEY": settings.MOOLRE_PUB_KEY,
        "Content-Type": "application/json",
    }


def _moolre_base_url() -> str:
    if settings.MOOLRE_SANDBOX:
        return "https://sandbox.moolre.com"
    return "https://api.moolre.com"


def _ensure_configured():
    if not settings.MOOLRE_USER or not settings.MOOLRE_PUB_KEY or not settings.MOOLRE_ACCOUNT_ID:
        raise MoolreNotConfigured()


def _request_timeout() -> float:
    return max(3.0, float(getattr(settings, "MOOLRE_REQUEST_TIMEOUT_SECONDS", 30)))


def _status_timeout() -> float:
    return max(3.0, float(getattr(settings, "MOOLRE_STATUS_TIMEOUT_SECONDS", 8)))


def _pos_business_email(fallback: str = "") -> str:
    return (
        getattr(settings, "MOOLRE_POS_EMAIL", "")
        or fallback
        or "payments@afrigaint.com"
    ).strip()


def _pos_redirect_url() -> str:
    return (
        getattr(settings, "MOOLRE_POS_REDIRECT_URL", "")
        or f"{settings.FRONTEND_URL.rstrip('/')}/deposit/success"
    ).strip()


def _pos_expiration_minutes() -> int:
    return max(1, int(getattr(settings, "MOOLRE_POS_EXPIRATION_MINUTES", 60)))


def extract_externalref(payload: dict) -> str:
    data = payload.get("data")
    if isinstance(data, str) and data.strip():
        return data.strip()
    if isinstance(data, dict):
        for key in ("externalref", "reference", "id"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return str(payload.get("externalref") or payload.get("reference") or "").strip()


def extract_provider_reference(payload: dict) -> str:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("thirdpartyref", "transactionid", "reference"):
            value = str(data.get(key) or "").strip()
            if value:
                return value
    return str(payload.get("reference") or "").strip()


def create_pos_payment_link(
    amount,
    *,
    externalref: str,
    email: str = "",
    redirect_url: str = "",
    metadata: dict | None = None,
) -> dict:
    """Create a hosted Moolre POS checkout URL for a deposit."""
    _ensure_configured()

    payload = {
        "type": 1,
        "amount": str(amount),
        "email": _pos_business_email(email),
        "externalref": externalref,
        "callback": settings.MOOLRE_WEBHOOK_URL,
        "redirect": redirect_url or _pos_redirect_url(),
        "reusable": "0",
        "expiration_time": _pos_expiration_minutes(),
        "currency": "GHS",
        "accountnumber": settings.MOOLRE_ACCOUNT_ID,
        "metadata": metadata or {},
    }

    url = f"{_moolre_base_url()}/embed/link"
    try:
        response = requests.post(url, json=payload, headers=_moolre_headers(), timeout=_request_timeout())
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Moolre POS link request failed for %s", externalref)
        raise MoolreError("Unable to reach payment provider. Try again.", code="provider_unreachable") from exc

    data = response.json()
    logger.info("Moolre POS link response for %s: %s", externalref, data)

    if str(data.get("status")) in {"1", "true"} and str(data.get("code", "")).upper() == "POS09":
        link_data = data.get("data") or {}
        payment_url = str(link_data.get("authorization_url") or "").strip()
        if not payment_url:
            raise MoolreError(
                "Payment page could not be created.",
                code="payment_url_missing",
                response=data,
            )
        return {
            "payment_status": "pending",
            "message": "Complete payment on the Moolre checkout page.",
            "payment_url": payment_url,
            "provider_reference": str(link_data.get("reference") or ""),
            "provider_response": data,
        }

    if str(data.get("status")) in {"0", "false"}:
        raise MoolreError(
            data.get("message") or "Payment page could not be created.",
            code=str(data.get("code") or "payment_failed"),
            response=data,
        )

    raise MoolreError(
        data.get("message") or "Unexpected response from payment provider.",
        code=str(data.get("code") or "payment_failed"),
        response=data,
    )


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
        response = requests.post(
            url,
            json=payload,
            headers=_moolre_headers(),
            timeout=_status_timeout(),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception("Moolre payment status request failed")
        raise MoolreError("Unable to verify payment status.", code="provider_unreachable") from exc

    return response.json()


def is_successful_status_response(data: dict) -> bool:
    if str(data.get("status")) in {"1", "true"}:
        code = str(data.get("code", "")).upper()
        if code in MOOLRE_SUCCESS_CODES:
            return True
    tx = data.get("data") or {}
    if isinstance(tx, dict) and str(tx.get("txstatus")) == "1":
        return True
    return False
