from decimal import Decimal

from backend.accounts.models import Currency

MIN_DEPOSIT = {
    Currency.GHS: Decimal("1"),
    Currency.NGN: Decimal("3000"),
}

BONUS_THRESHOLD = {
    Currency.GHS: Decimal("3000"),
    Currency.NGN: Decimal("300000"),
}

BONUS_RATE = Decimal("0.5")


def get_min_deposit(currency: str) -> Decimal:
    return MIN_DEPOSIT.get(currency, MIN_DEPOSIT[Currency.GHS])


def get_bonus_threshold(currency: str) -> Decimal:
    return BONUS_THRESHOLD.get(currency, BONUS_THRESHOLD[Currency.GHS])


def calculate_deposit_bonus(amount: Decimal, currency: str) -> Decimal:
    threshold = get_bonus_threshold(currency)
    if amount >= threshold:
        return (amount * BONUS_RATE).quantize(Decimal("0.01"))
    return Decimal("0")


def calculate_deposit_credit(amount: Decimal, currency: str) -> tuple[Decimal, Decimal]:
    """
    Return (bonus_amount, display_credit).

    Display credit is always 1:1 with the deposited amount. The bonus is calculated
    separately for reporting/promotions and is not added to the user's balance.
    """
    bonus = calculate_deposit_bonus(amount, currency)
    credited = amount.quantize(Decimal("0.01"))
    return bonus, credited
