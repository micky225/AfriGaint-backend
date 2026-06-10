import hashlib
import secrets
from datetime import timedelta

from django.utils import timezone


def normalize_phone(country_code: str, phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    code = country_code.strip()
    if not code.startswith("+"):
        code = f"+{code}"
    return f"{code}{digits}"


def generate_otp_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def otp_expiry(minutes: int = 10):
    return timezone.now() + timedelta(minutes=minutes)


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)
