import uuid
from decimal import Decimal

from django.conf import settings
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
from backend.accounts.services.paystack import (
    PaystackError,
    extract_provider_reference_from_webhook,
    extract_reference_from_webhook,
    initialize_transaction,
    is_charge_success_webhook,
    is_successful_verify_response,
    verify_transaction,
)
# Moolre integration disabled — kept for reference.
# from backend.accounts.services.moolre import (
#     MoolreError,
#     create_pos_payment_link,
#     extract_externalref,
#     extract_provider_reference,
#     is_successful_status_response,
# )
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


def _validate_deposit_amount(account: MyAccount, amount: Decimal) -> str:
    if amount <= 0:
        raise DepositError("Deposit amount must be greater than zero.")

    currency = account.currency or Currency.GHS
    minimum = get_next_deposit_minimum(currency, account.withdrawal_deposit_count)
    if amount < minimum:
        raise DepositError(
            f"Minimum deposit for this step is {minimum} {currency}.",
            code="below_minimum",
        )
    return currency


def _manual_reference_for_account(account: MyAccount) -> str:
    digits = "".join(ch for ch in account.user.phone if ch.isdigit())
    user_hint = digits[-6:] if len(digits) >= 6 else digits or str(account.user_id)
    return f"DEP-NGN-{user_hint}-{uuid.uuid4().hex[:6].upper()}"


def _deposit_result_payload(
    account: MyAccount,
    *,
    reference: str,
    amount: Decimal,
    bonus: Decimal,
    total_credit: Decimal,
    currency: str,
    tx: AccountTransaction,
    payment_status: str = "completed",
    message: str = "",
    provider_reference: str = "",
    session_id: str = "",
    payment_url: str = "",
    access_code: str = "",
    paystack_public_key: str = "",
) -> dict:
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
        "payment_status": payment_status,
        "message": message,
        "provider_reference": provider_reference,
        "session_id": session_id,
        "payment_url": payment_url,
        "access_code": access_code,
        "paystack_public_key": paystack_public_key,
    }


def complete_deposit(
    account: MyAccount,
    amount: Decimal,
    *,
    method: str = PaymentMethod.MOMO,
    provider: str = "moolre",
    phone_number: str = "",
    tx: AccountTransaction | None = None,
) -> dict:
    """Credit display balance after real payment is confirmed."""
    currency = _validate_deposit_amount(account, amount)
    bonus, total_credit = calculate_deposit_credit(amount, currency)

    tier = min(account.withdrawal_deposit_count + 1, REQUIRED_DEPOSITS_FOR_WITHDRAWAL)
    note = f"Deposit step {tier} — display credit (1:1)"
    if bonus > 0:
        note = (
            f"Deposit step {tier} — display credit (1:1); "
            f"bonus eligible (+{bonus} {currency}) not applied to balance"
        )

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)

        if tx is None:
            reference = f"DEP-{uuid.uuid4().hex[:12].upper()}"
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
        else:
            locked_tx = AccountTransaction.objects.select_for_update().get(pk=tx.pk)
            if locked_tx.status == TransactionStatus.COMPLETED:
                return _deposit_result_payload(
                    account,
                    reference=locked_tx.reference,
                    amount=locked_tx.amount,
                    bonus=bonus,
                    total_credit=locked_tx.net_amount,
                    currency=currency,
                    tx=locked_tx,
                    payment_status="completed",
                )

            reference = locked_tx.reference
            locked_tx.status = TransactionStatus.COMPLETED
            locked_tx.net_amount = total_credit
            locked_tx.note = note
            locked_tx.processed_at = timezone.now()
            locked_tx.save(update_fields=["status", "net_amount", "note", "processed_at"])
            tx = locked_tx

        if account.withdrawal_deposit_count < REQUIRED_DEPOSITS_FOR_WITHDRAWAL:
            account.withdrawal_deposit_count += 1

        account.current_balance = (account.current_balance + total_credit).quantize(Decimal("0.01"))
        account.save(
            update_fields=["current_balance", "withdrawal_deposit_count", "updated_at"]
        )

    return _deposit_result_payload(
        account,
        reference=reference,
        amount=amount,
        bonus=bonus,
        total_credit=total_credit,
        currency=currency,
        tx=tx,
        payment_status="completed",
        message="Deposit successful.",
    )


def process_deposit(
    account: MyAccount,
    amount: Decimal,
    *,
    method: str = PaymentMethod.MOMO,
    provider: str = "",
    phone_number: str = "",
) -> dict:
    """Direct completion helper used by tests and internal flows."""
    return complete_deposit(
        account,
        amount,
        method=method,
        provider=provider or "manual",
        phone_number=phone_number,
    )


def initiate_deposit_payment(
    account: MyAccount,
    amount: Decimal,
    *,
    phone_number: str = "",
) -> dict:
    currency = _validate_deposit_amount(account, amount)
    if currency != Currency.GHS:
        raise DepositError(
            "Online deposits are only available for GHS accounts.",
            code="unsupported_currency",
        )
    payer_phone = phone_number or account.user.phone
    bonus, total_credit = calculate_deposit_credit(amount, currency)
    reference = f"DEP-{uuid.uuid4().hex[:12].upper()}"

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)
        _validate_deposit_amount(account, amount)

        tx = AccountTransaction.objects.create(
            account=account,
            tx_type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            method=PaymentMethod.MOMO,
            amount=amount,
            fee=Decimal("0"),
            net_amount=amount,
            reference=reference,
            note="Awaiting Paystack payment",
        )
        Deposit.objects.create(
            transaction=tx,
            phone_number=payer_phone,
            provider="paystack",
        )

    try:
        provider_result = initialize_transaction(
            email=account.user.email or f"{account.user.phone}@afrigaint.com",
            amount=amount,
            reference=reference,
            metadata={"account_id": account.pk, "user_id": account.user_id},
        )
    except PaystackError as exc:
        fail_deposit_by_reference(reference, reason=exc.message)
        raise DepositError(exc.message, code=exc.code) from exc

    provider_reference = provider_result.get("provider_reference") or ""
    payment_url = provider_result.get("payment_url") or ""
    access_code = provider_result.get("access_code") or ""

    if provider_reference:
        AccountTransaction.objects.filter(pk=tx.pk, provider_reference="").update(
            provider_reference=provider_reference
        )

    return _deposit_result_payload(
        account,
        reference=reference,
        amount=amount,
        bonus=bonus,
        total_credit=total_credit,
        currency=currency,
        tx=tx,
        payment_status="pending",
        message=provider_result.get("message") or "Complete payment on the Paystack checkout page.",
        provider_reference=provider_reference,
        payment_url=payment_url,
        access_code=access_code,
        paystack_public_key=getattr(settings, "PAYSTACK_PUBLIC_KEY", ""),
    )


def submit_manual_bank_deposit(
    account: MyAccount,
    amount: Decimal,
    *,
    transaction_id: str,
) -> dict:
    currency = _validate_deposit_amount(account, amount)
    if currency != Currency.NGN:
        raise DepositError(
            "Manual bank confirmation is only enabled for NGN accounts.",
            code="unsupported_currency",
        )

    normalized_tx_id = (transaction_id or "").strip()
    if not normalized_tx_id:
        raise DepositError("Transaction ID is required.", code="transaction_id_required")

    existing = AccountTransaction.objects.filter(
        tx_type=TransactionType.DEPOSIT,
        method=PaymentMethod.BANK,
        provider_reference__iexact=normalized_tx_id,
    ).first()
    if existing and existing.account_id != account.id:
        raise DepositError(
            "This transaction ID is already linked to another account.",
            code="transaction_id_in_use",
        )

    bonus, total_credit = calculate_deposit_credit(amount, currency)
    note = f"Manual NGN deposit submitted by {account.user.phone}"
    reference = _manual_reference_for_account(account)

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)
        _validate_deposit_amount(account, amount)

        tx = AccountTransaction.objects.create(
            account=account,
            tx_type=TransactionType.DEPOSIT,
            status=TransactionStatus.PENDING,
            method=PaymentMethod.BANK,
            amount=amount,
            fee=Decimal("0"),
            net_amount=total_credit,
            reference=reference,
            provider_reference=normalized_tx_id,
            note=note[:255],
        )
        Deposit.objects.create(
            transaction=tx,
            phone_number=account.user.phone,
            provider="manual_bank_ngn",
        )

    return _deposit_result_payload(
        account,
        reference=reference,
        amount=amount,
        bonus=bonus,
        total_credit=total_credit,
        currency=currency,
        tx=tx,
        payment_status="pending",
        message="Deposit submitted. Waiting for admin confirmation.",
        provider_reference=normalized_tx_id,
    )


def fail_deposit_by_reference(reference: str, *, reason: str = "") -> AccountTransaction | None:
    with transaction.atomic():
        try:
            tx = AccountTransaction.objects.select_for_update().get(
                reference=reference,
                tx_type=TransactionType.DEPOSIT,
            )
        except AccountTransaction.DoesNotExist:
            return None

        if tx.status != TransactionStatus.PENDING:
            return tx

        tx.status = TransactionStatus.FAILED
        tx.note = reason or "Payment failed"
        tx.processed_at = timezone.now()
        tx.save(update_fields=["status", "note", "processed_at"])
        return tx


def complete_deposit_by_reference(reference: str, *, provider_reference: str = "") -> dict | None:
    with transaction.atomic():
        try:
            tx = AccountTransaction.objects.select_for_update().select_related("account").get(
                reference=reference,
                tx_type=TransactionType.DEPOSIT,
            )
        except AccountTransaction.DoesNotExist:
            return None

        if tx.status == TransactionStatus.COMPLETED:
            account = tx.account
            currency = account.currency or Currency.GHS
            completed_bonus, _ = calculate_deposit_credit(tx.amount, currency)
            return _deposit_result_payload(
                account,
                reference=tx.reference,
                amount=tx.amount,
                bonus=completed_bonus,
                total_credit=tx.net_amount,
                currency=currency,
                tx=tx,
                payment_status="completed",
                provider_reference=tx.provider_reference,
            )

        if tx.status != TransactionStatus.PENDING:
            return None

        if provider_reference and not tx.provider_reference:
            tx.provider_reference = provider_reference
            tx.save(update_fields=["provider_reference"])

    return complete_deposit(
        tx.account,
        tx.amount,
        phone_number=tx.deposit.phone_number,
        provider="paystack",
        tx=tx,
    )


def sync_deposit_status(reference: str) -> dict:
    try:
        tx = AccountTransaction.objects.select_related("account").get(
            reference=reference,
            tx_type=TransactionType.DEPOSIT,
        )
    except AccountTransaction.DoesNotExist:
        raise DepositError("Deposit not found.", code="deposit_not_found") from None

    if tx.status == TransactionStatus.COMPLETED:
        currency = tx.account.currency or Currency.GHS
        completed_bonus, _ = calculate_deposit_credit(tx.amount, currency)
        return _deposit_result_payload(
            tx.account,
            reference=tx.reference,
            amount=tx.amount,
            bonus=completed_bonus,
            total_credit=tx.net_amount,
            currency=currency,
            tx=tx,
            payment_status="completed",
            message="Deposit successful.",
            provider_reference=tx.provider_reference,
        )

    if tx.status != TransactionStatus.PENDING:
        raise DepositError("This deposit is no longer pending.", code="deposit_not_pending")

    deposit = getattr(tx, "deposit", None)
    if deposit and deposit.provider == "manual_bank_ngn":
        pending_bonus, pending_credit = calculate_deposit_credit(
            tx.amount,
            tx.account.currency or Currency.GHS,
        )
        return _deposit_result_payload(
            tx.account,
            reference=tx.reference,
            amount=tx.amount,
            bonus=pending_bonus,
            total_credit=pending_credit,
            currency=tx.account.currency or Currency.GHS,
            tx=tx,
            payment_status="pending",
            message="Deposit is pending admin confirmation.",
            provider_reference=tx.provider_reference,
        )

    try:
        status_data = verify_transaction(reference)
    except PaystackError as exc:
        raise DepositError(exc.message, code=exc.code) from exc

    if is_successful_verify_response(status_data):
        data = status_data.get("data") or {}
        provider_reference = str(data.get("id") or data.get("reference") or "")
        return complete_deposit_by_reference(reference, provider_reference=provider_reference)

    data = status_data.get("data") or {}
    if str(data.get("status", "")).lower() == "failed":
        fail_deposit_by_reference(reference, reason="Payment failed")
        raise DepositError("Payment failed.", code="payment_failed")

    pending_bonus, pending_credit = calculate_deposit_credit(
        tx.amount,
        tx.account.currency or Currency.GHS,
    )
    return _deposit_result_payload(
        tx.account,
        reference=tx.reference,
        amount=tx.amount,
        bonus=pending_bonus,
        total_credit=pending_credit,
        currency=tx.account.currency or Currency.GHS,
        tx=tx,
        payment_status="pending",
        message=status_data.get("message") or "Payment is still pending.",
        provider_reference=tx.provider_reference,
    )


def handle_paystack_webhook(payload: dict) -> dict | None:
    reference = extract_reference_from_webhook(payload)
    provider_reference = extract_provider_reference_from_webhook(payload)

    if not is_charge_success_webhook(payload):
        if reference and payload.get("event") in {"charge.failed", "transfer.failed"}:
            fail_deposit_by_reference(reference, reason=payload.get("message") or "Payment failed")
        return None

    if not reference:
        return None

    return complete_deposit_by_reference(reference, provider_reference=provider_reference)
