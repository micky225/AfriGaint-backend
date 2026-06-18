from django.contrib.auth import get_user_model
from decimal import Decimal
import logging

from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from backend.accounts.models import BetTicket, Currency, Deposit, MyAccount, PayoutSetting, Withdrawal
from backend.accounts.serializers import (
    AccountSummarySerializer,
    AccountTransactionSerializer,
    BetTicketSerializer,
    BookingSlipSerializer,
    CreateDepositSerializer,
    CreatePayoutSettingSerializer,
    CreateWithdrawalSerializer,
    DepositResultSerializer,
    DepositSerializer,
    LoginSerializer,
    PasswordResetSerializer,
    PayoutSettingSerializer,
    PlaceBetSerializer,
    PlacedBetResultSerializer,
    RegisterSerializer,
    UserSerializer,
    WithdrawalResultSerializer,
    WithdrawalSerializer,
)
from backend.accounts.services.betting import BettingError, load_booking_slip, place_bet
from backend.accounts.services.deposit import (
    DepositError,
    handle_paystack_webhook,
    initiate_deposit_payment,
    submit_manual_bank_deposit,
    sync_deposit_status,
)
from backend.accounts.services.paystack import extract_reference_from_webhook, verify_webhook_signature
from backend.accounts.services.withdrawal import (
    WithdrawalError,
    get_total_pending_withdrawal,
    process_withdrawal,
)
from backend.accounts.withdrawal_rules import get_withdrawal_gate, get_withdrawal_lock_count

User = get_user_model()
logger = logging.getLogger(__name__)


class AuthRateThrottle(AnonRateThrottle):
    rate = '60/minute'


def currency_for_country_code(country_code: str) -> str:
    normalized = (country_code or "").strip()
    if normalized in {"+234", "234"}:
        return Currency.NGN
    return Currency.GHS


def auth_response(user, message: str, status_code=status.HTTP_200_OK):
    token, _ = Token.objects.get_or_create(user=user)
    return Response(
        {
            "message": message,
            "token": token.key,
            "user": UserSerializer(user).data,
        },
        status=status_code,
    )


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = User.objects.create_user(
            phone=data["full_phone"],
            password=data["password"],
            email=data["email"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            is_phone_verified=True,
        )
        account, _ = MyAccount.objects.get_or_create(user=user)
        account.currency = currency_for_country_code(data["country_code"])
        referral_agent = data.get("referral_agent")
        if referral_agent:
            account.referred_by_agent = referral_agent
            account.referral_code_used = referral_agent.referral_code
        account.save(
            update_fields=["currency", "referred_by_agent", "referral_code_used", "updated_at"]
        )
        return Response(
            {"message": "Account created successfully."},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]
        return auth_response(user, "Logged in successfully.")


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        return Response({"message": "Logged out successfully."})


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"user": UserSerializer(request.user).data})


class PasswordResetView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [AuthRateThrottle]

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["full_phone"]

        user = User.objects.get(phone=phone)
        user.set_password(serializer.validated_data["password"])
        user.save(update_fields=["password"])

        return Response({"message": "Password reset successfully."})


class AccountSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        gate = get_withdrawal_gate(
            account.currency,
            account.withdrawal_deposit_count,
            get_withdrawal_lock_count(account),
        )
        payload = {
            "phone": request.user.phone,
            "email": request.user.email,
            "first_name": request.user.first_name or "",
            "last_name": request.user.last_name or "",
            "currency": account.currency,
            "current_balance": account.current_balance,
            "locked_balance": account.locked_balance,
            "country": account.country,
            "city": account.city,
            "withdrawal_deposit_count": account.withdrawal_deposit_count,
            "withdrawals_unlocked": gate["withdrawals_unlocked"],
            "withdrawal_gate": gate,
            "total_pending_withdrawal": get_total_pending_withdrawal(account),
        }
        return Response(AccountSummarySerializer(payload).data)


class AccountTransactionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = request.user.my_account.transactions.all()[:100]
        return Response(AccountTransactionSerializer(qs, many=True).data)


class AccountDepositsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        qs = Deposit.objects.filter(transaction__account=account).select_related("transaction")[:100]
        return Response(DepositSerializer(qs, many=True).data)

    def post(self, request):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        serializer = CreateDepositSerializer(data=request.data, context={"account": account})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if (account.currency or Currency.GHS) == Currency.NGN:
            try:
                result = submit_manual_bank_deposit(
                    account,
                    data["amount"],
                    transaction_id=data.get("transaction_id", ""),
                )
            except DepositError as exc:
                return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

            return Response(
                {
                    "message": result.get("message") or "Deposit submitted. Waiting for admin confirmation.",
                    "deposit": DepositResultSerializer(result).data,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        try:
            result = initiate_deposit_payment(
                account,
                data["amount"],
                phone_number=data.get("phone_number", ""),
            )
        except DepositError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        payment_status = result.get("payment_status", "pending")
        if payment_status == "completed":
            message = "Deposit successful."
            if Decimal(str(result.get("bonus_amount", 0))) > 0:
                message = (
                    f"Deposit successful. Display credit is {result['total_credited']} "
                    f"{result['currency']} (1:1 with your payment)."
                )
            status_code = status.HTTP_201_CREATED
        else:
            message = result.get("message") or "Complete payment on the Paystack checkout page."
            status_code = status.HTTP_202_ACCEPTED

        return Response(
            {
                "message": message,
                "deposit": DepositResultSerializer(result).data,
            },
            status=status_code,
        )


class DepositOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        currency = account.currency or Currency.GHS
        minimum = str(
            get_withdrawal_gate(
                currency,
                account.withdrawal_deposit_count,
                get_withdrawal_lock_count(account),
            )["next_deposit_minimum"]
        )

        if currency == Currency.NGN:
            return Response(
                {
                    "currency": currency,
                    "mode": "manual_bank",
                    "minimum_amount": minimum,
                    "required_fields": ["amount", "transaction_id"],
                    "note": "Submit your transfer reference for admin confirmation.",
                }
            )

        from django.conf import settings

        return Response(
            {
                "currency": currency,
                "mode": "paystack",
                "minimum_amount": minimum,
                "required_fields": ["amount"],
                "paystack_public_key": settings.PAYSTACK_PUBLIC_KEY,
                "note": "You will be redirected to Paystack to complete your deposit.",
            }
        )


class DepositStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, reference):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        if not Deposit.objects.filter(
            transaction__reference=reference,
            transaction__account=account,
        ).exists():
            return Response({"detail": "Deposit not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = sync_deposit_status(reference)
        except DepositError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        message = result.get("message") or "Deposit status updated."
        if result.get("payment_status") == "completed":
            message = "Deposit successful."

        return Response(
            {
                "message": message,
                "deposit": DepositResultSerializer(result).data,
            }
        )


class PaystackPaymentWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.headers.get("x-paystack-signature", "")
        if not verify_webhook_signature(request.body, signature):
            logger.warning("Paystack webhook rejected: invalid signature")
            return Response({"detail": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST)

        payload = request.data if isinstance(request.data, dict) else {}
        reference = extract_reference_from_webhook(payload)
        logger.info("Paystack webhook received for %s event=%s", reference or "unknown", payload.get("event"))

        result = handle_paystack_webhook(payload)
        if result:
            logger.info("Paystack webhook completed deposit %s", result["reference"])
            return Response(
                {"message": "Deposit completed.", "reference": result["reference"]},
                status=status.HTTP_200_OK,
            )
        return Response({"message": "Webhook received."}, status=status.HTTP_200_OK)


# Moolre webhook disabled — kept for reference.
# class MoolrePaymentWebhookView(APIView):
#     permission_classes = [AllowAny]
#     authentication_classes = []
#
#     def post(self, request):
#         ...


class AccountWithdrawalsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            Withdrawal.objects.filter(transaction__account=request.user.my_account)
            .select_related("transaction")
            .order_by("-transaction__created_at")[:100]
        )
        return Response(WithdrawalSerializer(qs, many=True).data)

    def post(self, request):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        serializer = CreateWithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            payout_setting = PayoutSetting.objects.get(
                pk=data["payout_setting_id"],
                account=account,
                is_active=True,
            )
        except PayoutSetting.DoesNotExist:
            return Response(
                {"detail": "Payout details not found. Add them under payout settings."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = process_withdrawal(account, data["amount"], payout_setting=payout_setting)
        except WithdrawalError as exc:
            payload = {"detail": exc.message, "code": exc.code}
            if exc.prompt:
                payload["withdrawal_prompt"] = exc.prompt
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

        if result.get("locked"):
            return Response(
                {
                    "message": result["message"],
                    "locked": True,
                    "withdrawal_prompt": result.get("withdrawal_prompt"),
                    "withdrawal": WithdrawalResultSerializer(result).data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "message": result["message"],
                "locked": False,
                "withdrawal": WithdrawalResultSerializer(result).data,
            },
            status=status.HTTP_201_CREATED,
        )


class AccountBetsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from backend.app.models import Match

        account, _ = MyAccount.objects.get_or_create(user=request.user)
        qs = BetTicket.objects.filter(account=account).prefetch_related("legs")
        scope = (request.query_params.get("scope") or "").strip().lower()
        if scope == "open":
            qs = qs.filter(status="open")
        elif scope == "settled":
            qs = qs.exclude(status="open")

        tickets = list(qs[:100])
        match_ids = {leg.match_id for ticket in tickets for leg in ticket.legs.all()}
        match_cache = {
            match.event_id: match
            for match in Match.objects.filter(event_id__in=match_ids)
        }
        return Response(
            BetTicketSerializer(
                tickets,
                many=True,
                context={"match_cache": match_cache},
            ).data
        )

    def post(self, request):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        serializer = PlaceBetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        normalized_selections = []
        for item in data["selections"]:
            normalized_selections.append(
                {
                    "match_id": item["match_id"],
                    "match_label": item["match_label"],
                    "pick": item["pick"],
                    "selection_key": item.get("selection_key") or "",
                    "pick_side": item.get("pick_side") or "",
                    "odd": item["odd"],
                    "market": item.get("market") or "",
                    "market_key": item.get("market_key") or "",
                    "started": item.get("started", False),
                }
            )

        try:
            result = place_bet(account, data["stake"], normalized_selections)
        except BettingError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "message": "Bet placed successfully.",
                "ticket": PlacedBetResultSerializer(result).data,
            },
            status=status.HTTP_201_CREATED,
        )


class BookingCodeView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, code):
        try:
            payload = load_booking_slip(code)
        except BettingError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSlipSerializer(payload).data)


class AccountPayoutSettingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = PayoutSetting.objects.filter(account=request.user.my_account)[:20]
        return Response(PayoutSettingSerializer(qs, many=True).data)

    def post(self, request):
        account, _ = MyAccount.objects.get_or_create(user=request.user)
        serializer = CreatePayoutSettingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        PayoutSetting.objects.filter(account=account, is_default=True).update(is_default=False)
        setting, _ = PayoutSetting.objects.update_or_create(
            account=account,
            payout_type=data["payout_type"],
            defaults={
                "account_name": data["account_name"].strip(),
                "account_number": data["account_number"].strip(),
                "network": (data.get("network") or "").strip(),
                "provider_name": (data.get("provider_name") or "").strip(),
                "is_default": True,
                "is_active": True,
            },
        )
        return Response(
            {
                "message": "Payout details saved.",
                "payout_setting": PayoutSettingSerializer(setting).data,
            },
            status=status.HTTP_201_CREATED,
        )
