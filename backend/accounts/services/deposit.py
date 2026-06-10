import uuid
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from backend.accounts.deposit_rules import calculate_deposit_credit
from backend.accounts.models import (
    AccountTransaction,
    Currency,
    Deposit,
    MyAccount,
    PaymentMethod,
    TransactionStatus,
    TransactionType,
)
from backend.accounts.withdrawal_rules import (
    REQUIRED_DEPOSITS_FOR_WITHDRAWAL,
    get_next_deposit_minimum,
    get_withdrawal_gate,
)


class DepositError(Exception):
    def __init__(self, message: str, code: str = "invalid_deposit"):
        self.message = message
        self.code = code
        super().__init__(message)


def process_deposit(
    account: MyAccount,
    amount: Decimal,
    *,
    method: str = PaymentMethod.MOMO,
    provider: str = "",
    phone_number: str = "",
) -> dict:
    if amount <= 0:
        raise DepositError("Deposit amount must be greater than zero.")

    currency = account.currency or Currency.GHS
    minimum = get_next_deposit_minimum(currency, account.withdrawal_deposit_count)
    if amount < minimum:
        raise DepositError(
            f"Minimum deposit for this step is {minimum} {currency}.",
            code="below_minimum",
        )

    bonus, total_credit = calculate_deposit_credit(amount, currency)
    reference = f"DEP-{uuid.uuid4().hex[:12].upper()}"

    tier = min(account.withdrawal_deposit_count + 1, REQUIRED_DEPOSITS_FOR_WITHDRAWAL)
    note = f"Deposit step {tier} — display credit"
    if bonus > 0:
        note = f"Deposit step {tier} with 50% bonus (+{bonus} {currency}) — display credit"

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)

        tx = AccountTransaction.objects.create(
            account=account,
            tx_type=TransactionType.DEPOSIT,
            status=TransactionStatus.COMPLETED,
            method=method,
            amount=amount,
            fee=Decimal("0"),
            net_amount=total_credit,
            reference=reference,
            note=note,
            processed_at=timezone.now(),
        )
        Deposit.objects.create(
            transaction=tx,
            phone_number=phone_number or account.user.phone,
            provider=provider,
        )

        if account.withdrawal_deposit_count < REQUIRED_DEPOSITS_FOR_WITHDRAWAL:
            account.withdrawal_deposit_count += 1

        account.current_balance = (account.current_balance + total_credit).quantize(Decimal("0.01"))
        account.save(
            update_fields=["current_balance", "withdrawal_deposit_count", "updated_at"]
        )

    gate = get_withdrawal_gate(account.currency, account.withdrawal_deposit_count)

    return {
        "reference": reference,
        "amount": amount,
        "bonus_amount": bonus,
        "total_credited": total_credit,
        "currency": currency,
        "new_balance": account.current_balance,
        "transaction_id": tx.pk,
        "withdrawal_deposit_count": account.withdrawal_deposit_count,
        "withdrawals_unlocked": gate["withdrawals_unlocked"],
        "withdrawal_gate": gate,
    }
