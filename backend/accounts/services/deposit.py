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
from backend.accounts.services.moolre import (
    MoolreError,
    initiate_payment_flow,
    is_successful_status_response,
    resolve_moolre_channel,
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


def _validate_deposit_amount(account: MyAccount, amount: Decimal) -> str:
    if amount <= 0:
        raise DepositError("Deposit amount must be greater than zero.")

    currency = account.currency or Currency.GHS
    if currency != Currency.GHS:
        raise DepositError(
            "USSD deposits are only available for GHS accounts.",
            code="unsupported_currency",
        )

    minimum = get_next_deposit_minimum(currency, account.withdrawal_deposit_count)
    if amount < minimum:
        raise DepositError(
            f"Minimum deposit for this step is {minimum} {currency}.",
            code="below_minimum",
        )
    return currency


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
    network: str = "mtn",
    otp_code: str = "",
    reference: str = "",
    session_id: str = "",
) -> dict:
    currency = _validate_deposit_amount(account, amount)
    payer_phone = phone_number or account.user.phone
    channel = resolve_moolre_channel(network)
    bonus, total_credit = calculate_deposit_credit(amount, currency)
    deposit_record = None

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)
        _validate_deposit_amount(account, amount)

        if reference:
            try:
                tx = AccountTransaction.objects.select_for_update().get(
                    reference=reference,
                    account=account,
                    tx_type=TransactionType.DEPOSIT,
                )
            except AccountTransaction.DoesNotExist:
                raise DepositError("Deposit not found.", code="deposit_not_found") from None

            amount = tx.amount
            bonus, total_credit = calculate_deposit_credit(amount, currency)

            if tx.status == TransactionStatus.COMPLETED:
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
                    message="Deposit already completed.",
                    provider_reference=tx.provider_reference,
                )
            if tx.status != TransactionStatus.PENDING:
                raise DepositError("This deposit can no longer be updated.", code="deposit_not_pending")

            deposit_record, _ = Deposit.objects.get_or_create(
                transaction=tx,
                defaults={
                    "phone_number": payer_phone,
                    "provider": "moolre",
                },
            )
            if not payer_phone:
                payer_phone = deposit_record.phone_number or payer_phone
            if not session_id:
                session_id = deposit_record.session_id
        else:
            reference = f"DEP-{uuid.uuid4().hex[:12].upper()}"
            tx = AccountTransaction.objects.create(
                account=account,
                tx_type=TransactionType.DEPOSIT,
                status=TransactionStatus.PENDING,
                method=PaymentMethod.MOMO,
                amount=amount,
                fee=Decimal("0"),
                net_amount=amount,
                reference=reference,
                note="Awaiting USSD payment confirmation",
            )
            deposit_record = Deposit.objects.create(
                transaction=tx,
                phone_number=payer_phone,
                provider="moolre",
            )

    if otp_code and not session_id:
        raise DepositError(
            "Payment session expired. Start the deposit again.",
            code="session_required",
        )

    try:
        provider_result = initiate_payment_flow(
            payer_phone,
            amount,
            externalref=reference,
            channel=channel,
            otp_code=otp_code,
            session_id=session_id,
        )
    except MoolreError as exc:
        if not otp_code:
            fail_deposit_by_reference(reference, reason=exc.message)
        raise DepositError(exc.message, code=exc.code) from exc

    provider_reference = provider_result.get("provider_reference") or ""
    stored_session_id = provider_result.get("session_id") or session_id
    if deposit_record is None:
        deposit_record = Deposit.objects.filter(transaction__reference=reference).first()

    if stored_session_id and deposit_record and deposit_record.session_id != stored_session_id:
        deposit_record.session_id = stored_session_id
        deposit_record.save(update_fields=["session_id"])

    if provider_reference:
        AccountTransaction.objects.filter(pk=tx.pk, provider_reference="").update(
            provider_reference=provider_reference
        )

    payment_status = provider_result["payment_status"]
    message = provider_result.get("message") or ""

    return _deposit_result_payload(
        account,
        reference=reference,
        amount=amount,
        bonus=bonus,
        total_credit=total_credit,
        currency=currency,
        tx=tx,
        payment_status=payment_status,
        message=message,
        provider_reference=provider_reference,
        session_id=stored_session_id,
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
        provider="moolre",
        tx=tx,
    )


def sync_deposit_status(reference: str) -> dict:
    from backend.accounts.services.moolre import check_payment_status

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

    try:
        status_data = check_payment_status(reference)
    except MoolreError as exc:
        raise DepositError(exc.message, code=exc.code) from exc

    if is_successful_status_response(status_data):
        data = status_data.get("data") or {}
        provider_reference = ""
        if isinstance(data, dict):
            provider_reference = str(data.get("thirdpartyref") or data.get("transactionid") or "")
        return complete_deposit_by_reference(reference, provider_reference=provider_reference)

    txstatus = ""
    if isinstance(status_data.get("data"), dict):
        txstatus = str(status_data["data"].get("txstatus", ""))

    if txstatus == "2":
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


def handle_moolre_webhook(payload: dict) -> dict | None:
    if not is_successful_status_response(payload):
        data = payload.get("data") or {}
        externalref = ""
        if isinstance(data, dict):
            externalref = data.get("externalref") or ""
        if externalref:
            fail_deposit_by_reference(str(externalref), reason=payload.get("message") or "Payment failed")
        return None

    data = payload.get("data") or {}
    externalref = ""
    provider_reference = ""
    if isinstance(data, dict):
        externalref = str(data.get("externalref") or "")
        provider_reference = str(data.get("thirdpartyref") or data.get("transactionid") or "")

    if not externalref:
        return None

    return complete_deposit_by_reference(externalref, provider_reference=provider_reference)
