from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate, get_user_model
from rest_framework import serializers

from backend.accounts.deposit_rules import get_min_deposit
from backend.accounts.withdrawal_rules import get_next_deposit_minimum, get_withdrawal_gate
from backend.accounts.models import (
    AccountTransaction,
    BetHistory,
    BetLeg,
    BetTicket,
    Currency,
    Deposit,
    PaymentMethod,
    PayoutSetting,
    PayoutType,
    Withdrawal,
)
from backend.accounts.utils import normalize_phone
from backend.accounts.validators import validate_password_length

User = get_user_model()


class PhoneSerializer(serializers.Serializer):
    country_code = serializers.CharField(max_length=5)
    phone = serializers.CharField(max_length=20)

    def validate(self, attrs):
        attrs["full_phone"] = normalize_phone(attrs["country_code"], attrs["phone"])
        return attrs


class RegisterSendOtpSerializer(PhoneSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if User.objects.filter(phone=attrs["full_phone"]).exists():
            raise serializers.ValidationError({"phone": "An account with this phone already exists."})
        return attrs


class RegisterSerializer(PhoneSerializer):
    first_name = serializers.CharField(max_length=200)
    last_name = serializers.CharField(max_length=200)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    referral_code = serializers.CharField(
        max_length=20,
        required=False,
        allow_blank=True,
        default="",
    )

    def validate_password(self, value):
        validate_password_length(value)
        return value

    def validate_referral_code(self, value):
        code = (value or "").strip()
        if not code:
            return ""
        from backend.agents.models import ReferralAgent

        agent = ReferralAgent.objects.filter(referral_code__iexact=code, is_active=True).first()
        if not agent:
            raise serializers.ValidationError("Invalid referral code.")
        return agent.referral_code

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if User.objects.filter(phone=attrs["full_phone"]).exists():
            raise serializers.ValidationError({"phone": "An account with this phone already exists."})
        code = attrs.get("referral_code") or ""
        if code:
            from backend.agents.models import ReferralAgent

            attrs["referral_agent"] = ReferralAgent.objects.get(referral_code=code)
        else:
            attrs["referral_agent"] = None
        return attrs

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value


class LoginSerializer(PhoneSerializer):
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        user = authenticate(phone=attrs["full_phone"], password=attrs["password"])
        if not user:
            raise serializers.ValidationError("Invalid phone number or password.")
        if not user.is_active:
            raise serializers.ValidationError("This account is inactive.")
        attrs["user"] = user
        return attrs


class VerifyPhoneSerializer(PhoneSerializer):
    otp = serializers.CharField(min_length=4, max_length=6)


class PasswordResetSendOtpSerializer(PhoneSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not User.objects.filter(phone=attrs["full_phone"]).exists():
            raise serializers.ValidationError({"phone": "No account found with this phone number."})
        return attrs


class PasswordResetVerifySerializer(PhoneSerializer):
    otp = serializers.CharField(min_length=4, max_length=6)


class PasswordResetSerializer(PhoneSerializer):
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        validate_password_length(value)
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not User.objects.filter(phone=attrs["full_phone"]).exists():
            raise serializers.ValidationError({"phone": "No account found with this phone number."})
        return attrs


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "phone", "email", "first_name", "last_name", "is_phone_verified"]


class WithdrawalPromptSerializer(serializers.Serializer):
    code = serializers.CharField()
    title = serializers.CharField()
    message = serializers.CharField()
    required_deposit = serializers.CharField(required=False)


class WithdrawalGateSerializer(serializers.Serializer):
    withdrawal_deposit_count = serializers.IntegerField()
    withdrawals_unlocked = serializers.BooleanField()
    required_deposits_for_withdrawal = serializers.IntegerField()
    next_deposit_minimum = serializers.CharField()
    withdrawal_prompt = WithdrawalPromptSerializer(allow_null=True)
    pending_delivery_hours = serializers.IntegerField()


class AccountSummarySerializer(serializers.Serializer):
    phone = serializers.CharField()
    email = serializers.EmailField(allow_null=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    currency = serializers.CharField()
    current_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    locked_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    country = serializers.CharField(allow_blank=True)
    city = serializers.CharField(allow_blank=True)
    withdrawal_deposit_count = serializers.IntegerField()
    withdrawals_unlocked = serializers.BooleanField()
    withdrawal_gate = WithdrawalGateSerializer()
    total_pending_withdrawal = serializers.DecimalField(max_digits=14, decimal_places=2)


class AccountTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountTransaction
        fields = [
            "id",
            "reference",
            "tx_type",
            "status",
            "method",
            "amount",
            "fee",
            "net_amount",
            "created_at",
            "processed_at",
        ]


class CreateDepositSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    method = serializers.ChoiceField(
        choices=[PaymentMethod.MOMO, PaymentMethod.CRYPTO, PaymentMethod.BANK],
        default=PaymentMethod.MOMO,
    )
    provider = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    phone_number = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")

    def validate_amount(self, value):
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError) as exc:
            raise serializers.ValidationError("Enter a valid deposit amount.") from exc

        if amount <= 0:
            raise serializers.ValidationError("Deposit amount must be greater than zero.")

        account = self.context["account"]
        currency = account.currency or Currency.GHS
        minimum = get_next_deposit_minimum(currency, account.withdrawal_deposit_count)
        if amount < minimum:
            raise serializers.ValidationError(
                f"Minimum deposit for this step is {minimum} {currency}."
            )
        return amount.quantize(Decimal("0.01"))


class DepositResultSerializer(serializers.Serializer):
    reference = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    bonus_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_credited = serializers.DecimalField(max_digits=14, decimal_places=2)
    currency = serializers.CharField()
    new_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    transaction_id = serializers.IntegerField()
    withdrawal_deposit_count = serializers.IntegerField(required=False)
    withdrawals_unlocked = serializers.BooleanField(required=False)
    withdrawal_gate = WithdrawalGateSerializer(required=False)


class CreateWithdrawalSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    payout_setting_id = serializers.IntegerField()

    def validate_amount(self, value):
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError) as exc:
            raise serializers.ValidationError("Enter a valid withdrawal amount.") from exc
        if amount <= 0:
            raise serializers.ValidationError("Withdrawal amount must be greater than zero.")
        return amount.quantize(Decimal("0.01"))


class WithdrawalResultSerializer(serializers.Serializer):
    reference = serializers.CharField()
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    requested_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False
    )
    pending_included = serializers.DecimalField(
        max_digits=14, decimal_places=2, required=False
    )
    currency = serializers.CharField()
    new_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    locked_balance = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    status = serializers.CharField()
    transaction_id = serializers.IntegerField()
    message = serializers.CharField()
    pending_delivery_hours = serializers.IntegerField()
    locked = serializers.BooleanField(required=False)
    withdrawal_prompt = WithdrawalPromptSerializer(required=False, allow_null=True)


class DepositSerializer(serializers.ModelSerializer):
    transaction = AccountTransactionSerializer(read_only=True)

    class Meta:
        model = Deposit
        fields = ["id", "provider", "phone_number", "transaction"]


class WithdrawalSerializer(serializers.ModelSerializer):
    transaction = AccountTransactionSerializer(read_only=True)

    class Meta:
        model = Withdrawal
        fields = ["id", "account_name", "account_number", "provider", "transaction"]


class PayoutSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayoutSetting
        fields = [
            "id",
            "payout_type",
            "account_name",
            "account_number",
            "provider_name",
            "network",
            "is_default",
            "is_active",
            "created_at",
        ]


class CreatePayoutSettingSerializer(serializers.Serializer):
    payout_type = serializers.ChoiceField(choices=PayoutType.choices)
    account_name = serializers.CharField(max_length=120)
    account_number = serializers.CharField(max_length=80)
    network = serializers.CharField(max_length=80, required=False, allow_blank=True, default="")
    provider_name = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        payout_type = attrs["payout_type"]
        if payout_type == PayoutType.MOMO and not (attrs.get("network") or "").strip():
            raise serializers.ValidationError({"network": "Select a mobile money network."})
        if payout_type == PayoutType.BANK and not (attrs.get("provider_name") or "").strip():
            raise serializers.ValidationError({"provider_name": "Bank name is required."})
        return attrs


class BetHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BetHistory
        fields = [
            "id",
            "stake",
            "odd",
            "possible_win",
            "payout",
            "status",
            "placed_at",
            "settled_at",
            "match_label",
        ]


class BetSelectionInputSerializer(serializers.Serializer):
    id = serializers.CharField(max_length=160)
    match_id = serializers.CharField(max_length=120)
    match_label = serializers.CharField(max_length=255)
    pick = serializers.CharField(max_length=120)
    selection_key = serializers.CharField(max_length=80, required=False, allow_blank=True, allow_null=True)
    pick_side = serializers.CharField(max_length=16, required=False, allow_blank=True, allow_null=True)
    odd = serializers.DecimalField(max_digits=10, decimal_places=2)
    market = serializers.CharField(max_length=120, required=False, allow_blank=True, allow_null=True)
    market_key = serializers.CharField(max_length=80, required=False, allow_blank=True, allow_null=True)
    started = serializers.BooleanField(required=False, default=False)


class PlaceBetSerializer(serializers.Serializer):
    stake = serializers.DecimalField(max_digits=14, decimal_places=2)
    selections = BetSelectionInputSerializer(many=True)

    def validate_stake(self, value):
        if value <= 0:
            raise serializers.ValidationError("Stake must be greater than zero.")
        return value.quantize(Decimal("0.01"))

    def validate_selections(self, value):
        if not value:
            raise serializers.ValidationError("Add at least one selection.")
        return value


class BetLegSerializer(serializers.ModelSerializer):
    pick = serializers.CharField(source="selection_label")
    market = serializers.CharField(source="market_label")
    home_score = serializers.SerializerMethodField()
    away_score = serializers.SerializerMethodField()
    match_status = serializers.SerializerMethodField()

    class Meta:
        model = BetLeg
        fields = [
            "match_id",
            "match_label",
            "pick",
            "pick_side",
            "market",
            "market_key",
            "odd",
            "status",
            "home_score",
            "away_score",
            "match_status",
        ]

    def _match_for_leg(self, leg: BetLeg):
        cache = self.context.get("match_cache") or {}
        return cache.get(leg.match_id)

    def get_match_status(self, leg: BetLeg):
        match = self._match_for_leg(leg)
        return match.status if match else None

    def get_home_score(self, leg: BetLeg):
        match = self._match_for_leg(leg)
        if not match or match.status != "finished":
            return None
        return match.home_score

    def get_away_score(self, leg: BetLeg):
        match = self._match_for_leg(leg)
        if not match or match.status != "finished":
            return None
        return match.away_score


class BetTicketSerializer(serializers.ModelSerializer):
    legs = BetLegSerializer(many=True, read_only=True)

    class Meta:
        model = BetTicket
        fields = [
            "id",
            "booking_code",
            "stake",
            "total_odds",
            "possible_win",
            "payout",
            "status",
            "placed_at",
            "settled_at",
            "legs",
        ]


class PlacedBetResultSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    booking_code = serializers.CharField()
    stake = serializers.DecimalField(max_digits=14, decimal_places=2)
    total_odds = serializers.DecimalField(max_digits=12, decimal_places=4)
    possible_win = serializers.DecimalField(max_digits=14, decimal_places=2)
    status = serializers.CharField()
    placed_at = serializers.DateTimeField()
    new_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    legs = BetSelectionInputSerializer(many=True)


class BookingSlipSerializer(serializers.Serializer):
    booking_code = serializers.CharField()
    stake = serializers.DecimalField(max_digits=14, decimal_places=2)
    selections = BetSelectionInputSerializer(many=True)
