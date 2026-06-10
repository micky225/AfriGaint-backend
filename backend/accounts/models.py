from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from .managers import AccountManager


class User(AbstractUser):
    username = None
    email = models.EmailField(null=True, blank=True, unique=True)
    first_name = models.CharField(max_length=200, null=True, blank=True)
    last_name = models.CharField(max_length=200, null=True, blank=True)
    phone = models.CharField(max_length=20, unique=True)
    is_phone_verified = models.BooleanField(default=False)
    date_joined = models.DateTimeField(verbose_name="date joined", auto_now_add=True)
    last_login = models.DateTimeField(verbose_name="last login", null=True, blank=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = []

    objects = AccountManager()

    def __str__(self):
        return self.phone


class OtpPurpose(models.TextChoices):
    PHONE_VERIFY = "phone_verify", "Phone verification"
    PASSWORD_RESET = "password_reset", "Password reset"


class PhoneOtp(models.Model):
    phone = models.CharField(max_length=20, db_index=True)
    code_hash = models.CharField(max_length=128)
    purpose = models.CharField(max_length=32, choices=OtpPurpose.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    reset_token = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class Currency(models.TextChoices):
    GHS = "GHS", "Ghana Cedi"
    NGN = "NGN", "Nigerian Naira"
    KES = "KES", "Kenyan Shilling"
    USD = "USD", "US Dollar"


class MyAccount(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="my_account")
    currency = models.CharField(max_length=5, choices=Currency.choices, default=Currency.GHS)
    current_balance = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Display balance shown to the user (dummy credits from deposits).",
    )
    locked_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    withdrawal_deposit_count = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of qualifying deposits completed toward withdrawal unlock (0-3).",
    )
    country = models.CharField(max_length=100, blank=True, default="")
    city = models.CharField(max_length=100, blank=True, default="")
    address = models.CharField(max_length=255, blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)
    referred_by_agent = models.ForeignKey(
        "agents.ReferralAgent",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )
    referral_code_used = models.CharField(max_length=20, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"MyAccount - {self.user.phone}"


class TransactionType(models.TextChoices):
    DEPOSIT = "deposit", "Deposit"
    WITHDRAW = "withdraw", "Withdraw"
    BET_STAKE = "bet_stake", "Bet Stake"
    BET_WIN = "bet_win", "Bet Win"
    REFUND = "refund", "Refund"
    ADJUSTMENT = "adjustment", "Adjustment"


class TransactionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    CANCELLED = "cancelled", "Cancelled"


class PaymentMethod(models.TextChoices):
    MOMO = "momo", "Mobile Money"
    BANK = "bank", "Bank Transfer"
    CARD = "card", "Card"
    CRYPTO = "crypto", "Crypto"
    MANUAL = "manual", "Manual"


class AccountTransaction(models.Model):
    account = models.ForeignKey(MyAccount, on_delete=models.CASCADE, related_name="transactions")
    tx_type = models.CharField(max_length=20, choices=TransactionType.choices)
    status = models.CharField(max_length=20, choices=TransactionStatus.choices, default=TransactionStatus.PENDING)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.MOMO)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    fee = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference = models.CharField(max_length=80, unique=True)
    provider_reference = models.CharField(max_length=120, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} - {self.tx_type}"


class Deposit(models.Model):
    transaction = models.OneToOneField(AccountTransaction, on_delete=models.CASCADE, related_name="deposit")
    phone_number = models.CharField(max_length=20, blank=True, default="")
    provider = models.CharField(max_length=60, blank=True, default="")

    def __str__(self):
        return f"Deposit - {self.transaction.reference}"


class Withdrawal(models.Model):
    transaction = models.OneToOneField(AccountTransaction, on_delete=models.CASCADE, related_name="withdrawal")
    account_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=80)
    provider = models.CharField(max_length=60, blank=True, default="")

    def __str__(self):
        return f"Withdrawal - {self.transaction.reference}"


class PayoutType(models.TextChoices):
    MOMO = "momo", "Mobile Money"
    BANK = "bank", "Bank Account"
    CRYPTO = "crypto", "Crypto Wallet"


class PayoutSetting(models.Model):
    account = models.ForeignKey(MyAccount, on_delete=models.CASCADE, related_name="payout_settings")
    payout_type = models.CharField(max_length=20, choices=PayoutType.choices, default=PayoutType.MOMO)
    account_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=80)
    provider_name = models.CharField(max_length=120, blank=True, default="")
    network = models.CharField(max_length=80, blank=True, default="")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.account.user.phone} - {self.payout_type}"


class BetStatus(models.TextChoices):
    OPEN = "open", "Open"
    WON = "won", "Won"
    LOST = "lost", "Lost"
    VOID = "void", "Void"
    CASHED_OUT = "cashed_out", "Cashed Out"


class BetHistory(models.Model):
    account = models.ForeignKey(MyAccount, on_delete=models.CASCADE, related_name="bets")
    match_label = models.CharField(max_length=255)
    market_label = models.CharField(max_length=120, blank=True, default="")
    selection_label = models.CharField(max_length=120, blank=True, default="")
    stake = models.DecimalField(max_digits=14, decimal_places=2)
    odd = models.DecimalField(max_digits=10, decimal_places=2)
    possible_win = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payout = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=BetStatus.choices, default=BetStatus.OPEN)
    placed_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-placed_at"]

    def __str__(self):
        return f"Bet #{self.pk} - {self.account.user.phone} - {self.status}"


class BetTicket(models.Model):
    account = models.ForeignKey(MyAccount, on_delete=models.CASCADE, related_name="bet_tickets")
    booking_code = models.CharField(max_length=12, unique=True, db_index=True)
    stake = models.DecimalField(max_digits=14, decimal_places=2)
    total_odds = models.DecimalField(max_digits=12, decimal_places=4)
    possible_win = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    payout = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=BetStatus.choices, default=BetStatus.OPEN)
    placed_at = models.DateTimeField(auto_now_add=True)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-placed_at"]

    def __str__(self):
        return f"Ticket {self.booking_code} - {self.account.user.phone}"


class LegStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    WON = "won", "Won"
    LOST = "lost", "Lost"
    VOID = "void", "Void"


class BetLeg(models.Model):
    ticket = models.ForeignKey(BetTicket, on_delete=models.CASCADE, related_name="legs")
    match_id = models.CharField(max_length=120)
    match_label = models.CharField(max_length=255)
    market_label = models.CharField(max_length=120, blank=True, default="")
    selection_label = models.CharField(max_length=120)
    selection_key = models.CharField(max_length=80, blank=True, default="")
    pick_side = models.CharField(max_length=16, blank=True, default="")
    market_key = models.CharField(max_length=80, blank=True, default="")
    odd = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=16, choices=LegStatus.choices, default=LegStatus.PENDING)
    settled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.match_label} - {self.selection_label}"


@receiver(post_save, sender=User)
def create_my_account(sender, instance, created, **kwargs):
    if created:
        MyAccount.objects.get_or_create(user=instance)
