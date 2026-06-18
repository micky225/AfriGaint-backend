import uuid
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from backend.accounts.models import (
    AccountTransaction,
    MyAccount,
    PaymentMethod,
    PayoutSetting,
    TransactionStatus,
    TransactionType,
    Withdrawal,
)
from backend.accounts.withdrawal_rules import (
    WITHDRAWAL_PENDING_HOURS,
    get_withdrawal_gate,
    get_withdrawal_lock_count,
)


def get_total_pending_withdrawal(account: MyAccount) -> Decimal:
    """Total amount pending delivery — merged WDR total, or locked reservations before final withdraw."""
    pending_qs = AccountTransaction.objects.filter(
        account=account,
        tx_type=TransactionType.WITHDRAW,
        status=TransactionStatus.PENDING,
    )
    real_pending_total = (
        pending_qs.filter(reference__startswith="WDR-")
        .exclude(reference__startswith="WDR-LOCK-")
        .aggregate(total=Sum("amount"))["total"]
    )
    if real_pending_total:
        return Decimal(real_pending_total).quantize(Decimal("0.01"))

    lock_pending_total = (
        pending_qs.filter(reference__startswith="WDR-LOCK").aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    return (account.locked_balance + Decimal(lock_pending_total)).quantize(Decimal("0.01"))


class WithdrawalError(Exception):
    def __init__(self, message: str, code: str = "withdrawal_blocked", prompt: dict | None = None):
        self.message = message
        self.code = code
        self.prompt = prompt
        super().__init__(message)


def _lock_withdrawal_attempt(
    account: MyAccount,
    amount: Decimal,
    *,
    payout_setting: PayoutSetting,
    gate: dict,
) -> dict:
    """Reserve a blocked withdrawal attempt — funds move to locked_balance (not stakeable)."""
    reference = f"WDR-LOCK-{uuid.uuid4().hex[:12].upper()}"
    note = (
        "Withdrawal amount reserved pending deposit steps. "
        "Complete the required deposits to proceed with delivery."
    )

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)

        if account.current_balance < amount:
            raise WithdrawalError(
                "Insufficient balance for this withdrawal.",
                code="insufficient_balance",
            )

        tx = AccountTransaction.objects.create(
            account=account,
            tx_type=TransactionType.WITHDRAW,
            status=TransactionStatus.PENDING,
            method=PaymentMethod.MOMO if payout_setting.payout_type == "momo" else PaymentMethod.BANK,
            amount=amount,
            fee=Decimal("0"),
            net_amount=amount,
            reference=reference,
            note=note,
        )
        Withdrawal.objects.create(
            transaction=tx,
            account_name=payout_setting.account_name,
            account_number=payout_setting.account_number,
            provider=payout_setting.provider_name or payout_setting.network,
        )

        account.current_balance = (account.current_balance - amount).quantize(Decimal("0.01"))
        account.locked_balance = (account.locked_balance + amount).quantize(Decimal("0.01"))
        account.save(update_fields=["current_balance", "locked_balance", "updated_at"])

    updated_gate = get_withdrawal_gate(
        account.currency,
        account.withdrawal_deposit_count,
        get_withdrawal_lock_count(account),
    )
    prompt = updated_gate["withdrawal_prompt"] or {}

    return {
        "locked": True,
        "reference": reference,
        "amount": amount,
        "currency": account.currency,
        "new_balance": account.current_balance,
        "locked_balance": account.locked_balance,
        "status": TransactionStatus.PENDING,
        "transaction_id": tx.pk,
        "message": prompt.get(
            "message",
            "Complete the required deposits before your withdrawal can be delivered.",
        ),
        "withdrawal_prompt": prompt,
        "pending_delivery_hours": WITHDRAWAL_PENDING_HOURS,
    }


def process_withdrawal(
    account: MyAccount,
    amount: Decimal,
    *,
    payout_setting: PayoutSetting,
) -> dict:
    if amount <= 0:
        raise WithdrawalError("Withdrawal amount must be greater than zero.", code="invalid_amount")

    account = MyAccount.objects.get(pk=account.pk)
    gate = get_withdrawal_gate(
        account.currency,
        account.withdrawal_deposit_count,
        get_withdrawal_lock_count(account),
    )

    if not gate["withdrawals_unlocked"]:
        return _lock_withdrawal_attempt(account, amount, payout_setting=payout_setting, gate=gate)

    if account.current_balance < amount:
        raise WithdrawalError(
            "Insufficient balance for this withdrawal.",
            code="insufficient_balance",
        )

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)

        if account.current_balance < amount:
            raise WithdrawalError(
                "Insufficient balance for this withdrawal.",
                code="insufficient_balance",
            )

        pending_included = account.locked_balance.quantize(Decimal("0.01"))
        total_amount = (amount + pending_included).quantize(Decimal("0.01"))

        reference = f"WDR-{uuid.uuid4().hex[:12].upper()}"
        if pending_included > 0:
            note = (
                f"Withdrawal successful and under review. {total_amount} {account.currency} "
                f"(including {pending_included} from pending withdrawals) will be delivered "
                f"within {WITHDRAWAL_PENDING_HOURS} hours."
            )
        else:
            note = (
                f"Withdrawal successful and under review. {total_amount} {account.currency} "
                f"will be delivered within {WITHDRAWAL_PENDING_HOURS} hours."
            )

        tx = AccountTransaction.objects.create(
            account=account,
            tx_type=TransactionType.WITHDRAW,
            status=TransactionStatus.PENDING,
            method=PaymentMethod.MOMO if payout_setting.payout_type == "momo" else PaymentMethod.BANK,
            amount=total_amount,
            fee=Decimal("0"),
            net_amount=total_amount,
            reference=reference,
            note=note,
        )
        Withdrawal.objects.create(
            transaction=tx,
            account_name=payout_setting.account_name,
            account_number=payout_setting.account_number,
            provider=payout_setting.provider_name or payout_setting.network,
        )

        if pending_included > 0:
            AccountTransaction.objects.filter(
                account=account,
                reference__startswith="WDR-LOCK",
                status=TransactionStatus.PENDING,
            ).update(
                status=TransactionStatus.COMPLETED,
                processed_at=timezone.now(),
                note=f"Merged into withdrawal {reference}",
            )

        account.current_balance = (account.current_balance - amount).quantize(Decimal("0.01"))
        account.locked_balance = Decimal("0")
        account.save(update_fields=["current_balance", "locked_balance", "updated_at"])

    return {
        "locked": False,
        "reference": reference,
        "amount": total_amount,
        "requested_amount": amount,
        "pending_included": pending_included,
        "currency": account.currency,
        "new_balance": account.current_balance,
        "locked_balance": account.locked_balance,
        "status": TransactionStatus.PENDING,
        "transaction_id": tx.pk,
        "message": note,
        "pending_delivery_hours": WITHDRAWAL_PENDING_HOURS,
    }
