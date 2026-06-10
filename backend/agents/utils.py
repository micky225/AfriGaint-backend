from django.conf import settings


def member_registration_url(referral_code: str) -> str:
    base = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
    return f"{base}/register?ref={referral_code}"
