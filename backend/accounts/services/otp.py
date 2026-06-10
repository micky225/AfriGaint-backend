from django.contrib.auth import get_user_model

from backend.accounts.models import OtpPurpose, PhoneOtp
from backend.accounts.services.twilio_sms import send_sms
from backend.accounts.utils import generate_otp_code, generate_reset_token, hash_otp, otp_expiry

User = get_user_model()

MAX_OTP_ATTEMPTS = 5


def invalidate_existing_otps(phone: str, purpose: str) -> None:
    PhoneOtp.objects.filter(phone=phone, purpose=purpose, is_used=False).update(is_used=True)


def create_and_send_otp(phone: str, purpose: str) -> tuple[PhoneOtp | None, str | None]:
    invalidate_existing_otps(phone, purpose)

    code = generate_otp_code()
    otp = PhoneOtp.objects.create(
        phone=phone,
        code_hash=hash_otp(code),
        purpose=purpose,
        expires_at=otp_expiry(),
    )

    if purpose == OtpPurpose.PHONE_VERIFY:
        message = f"Your AfriGaint verification code is {code}. It expires in 10 minutes."
    else:
        message = f"Your AfriGaint password reset code is {code}. It expires in 10 minutes."

    if not send_sms(phone, message):
        otp.delete()
        return None, "Failed to send verification code. Please try again."

    return otp, None


def verify_otp(phone: str, purpose: str, code: str) -> tuple[PhoneOtp | None, str | None]:
    otp = (
        PhoneOtp.objects.filter(phone=phone, purpose=purpose, is_used=False)
        .order_by("-created_at")
        .first()
    )

    if not otp:
        return None, "No verification code found. Please request a new one."
    if otp.is_expired:
        return None, "Verification code has expired. Please request a new one."
    if otp.attempts >= MAX_OTP_ATTEMPTS:
        return None, "Too many attempts. Please request a new code."

    otp.attempts += 1
    otp.save(update_fields=["attempts"])

    if otp.code_hash != hash_otp(code):
        return None, "Invalid verification code."

    otp.is_used = True
    if purpose == OtpPurpose.PASSWORD_RESET:
        otp.reset_token = generate_reset_token()
    otp.save(update_fields=["is_used", "reset_token"])
    return otp, None


def get_reset_otp_by_token(reset_token: str) -> PhoneOtp | None:
    return PhoneOtp.objects.filter(
        purpose=OtpPurpose.PASSWORD_RESET,
        reset_token=reset_token,
        is_used=True,
    ).first()
