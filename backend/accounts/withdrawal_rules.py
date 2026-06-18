from decimal import Decimal

from backend.accounts.models import Currency

DEPOSIT_TIER_MINIMUMS: dict[str, list[Decimal]] = {
    Currency.GHS: [Decimal("300"), Decimal("1000"), Decimal("2000")],
    Currency.NGN: [Decimal("30000"), Decimal("100000"), Decimal("200000")],
}

REQUIRED_DEPOSITS_FOR_WITHDRAWAL = 3
WITHDRAWAL_PENDING_HOURS = 72


def _currency(currency: str) -> str:
    return currency if currency in DEPOSIT_TIER_MINIMUMS else Currency.GHS


def get_tier_minimum(currency: str, tier_index: int) -> Decimal:
    tiers = DEPOSIT_TIER_MINIMUMS[_currency(currency)]
    index = max(0, min(tier_index, len(tiers) - 1))
    return tiers[index]


def get_next_deposit_minimum(currency: str, deposit_count: int) -> Decimal:
    tier_index = min(deposit_count, REQUIRED_DEPOSITS_FOR_WITHDRAWAL - 1)
    return get_tier_minimum(currency, tier_index)


def withdrawals_unlocked(deposit_count: int) -> bool:
    return deposit_count >= REQUIRED_DEPOSITS_FOR_WITHDRAWAL


def get_withdrawal_gate(currency: str, deposit_count: int) -> dict:
    currency = _currency(currency)
    unlocked = withdrawals_unlocked(deposit_count)
    next_minimum = get_next_deposit_minimum(currency, deposit_count)

    prompt = None
    if not unlocked:
        if deposit_count == 0:
            prompt = {
                "code": "need_first_deposit",
                "title": "Deposit required",
                "message": (
                    f"Make your first deposit of at least {next_minimum} {currency} "
                    "before you can withdraw."
                ),
                "required_deposit": str(next_minimum),
            }
        elif deposit_count == 1:
            tier_min = get_tier_minimum(currency, 1)
            prompt = {
                "code": "need_deposit_2",
                "title": "Withdrawal limit not reached",
                "message": (
                    f"You need to reach the withdrawal limit before a withdrawal can be approved. "
                    f"Please deposit at least {tier_min} {currency} to continue."
                ),
                "required_deposit": str(tier_min),
            }
        elif deposit_count == 2:
            tier_min = get_tier_minimum(currency, 2)
            prompt = {
                "code": "need_deposit_3",
                "title": "Almost there!",
                "message": (
                    f"You're almost there! Deposit at least {tier_min} {currency} more "
                    "to reach the withdrawal limit and unlock withdrawals."
                ),
                "required_deposit": str(tier_min),
            }

    return {
        "withdrawal_deposit_count": deposit_count,
        "withdrawals_unlocked": unlocked,
        "required_deposits_for_withdrawal": REQUIRED_DEPOSITS_FOR_WITHDRAWAL,
        "next_deposit_minimum": str(next_minimum),
        "withdrawal_prompt": prompt,
        "pending_delivery_hours": WITHDRAWAL_PENDING_HOURS,
    }
