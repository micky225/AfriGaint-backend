import secrets
import string
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from backend.app.services.market_resolver import (
    SUPPORTED_MARKET_KEYS,
    normalize_bet_selection,
)

from backend.accounts.models import (
    AccountTransaction,
    BetLeg,
    BetStatus,
    BetTicket,
    MyAccount,
    TransactionStatus,
    TransactionType,
)

BOOKING_CODE_ALPHABET = string.ascii_uppercase + string.digits
BOOKING_CODE_LENGTH = 6


class BettingError(Exception):
    def __init__(self, message: str, code: str = "bet_error"):
        self.message = message
        self.code = code
        super().__init__(message)


def generate_booking_code() -> str:
    for _ in range(32):
        code = "".join(secrets.choice(BOOKING_CODE_ALPHABET) for _ in range(BOOKING_CODE_LENGTH))
        if not BetTicket.objects.filter(booking_code=code).exists():
            return code
    raise BettingError("Could not generate a unique booking code.", code="booking_code_failed")


def calculate_total_odds(selections: list[dict]) -> Decimal:
    total = Decimal("1")
    for item in selections:
        odd = Decimal(str(item["odd"]))
        if odd <= 0:
            raise BettingError("Each selection must have a valid odd greater than zero.")
        total *= odd
    return total.quantize(Decimal("0.0001"))


def place_bet(account: MyAccount, stake: Decimal, selections: list[dict]) -> dict:
    if not selections:
        raise BettingError("Add at least one selection to place a bet.", code="empty_slip")
    if stake <= 0:
        raise BettingError("Stake must be greater than zero.", code="invalid_stake")
    if any(item.get("started") for item in selections):
        raise BettingError(
            "Remove selections that have already started before placing your bet.",
            code="started_selection",
        )

    total_odds = calculate_total_odds(selections)
    possible_win = (stake * total_odds).quantize(Decimal("0.01"))
    booking_code = generate_booking_code()
    reference = f"BET-{booking_code}"

    with transaction.atomic():
        account = MyAccount.objects.select_for_update().get(pk=account.pk)
        if account.current_balance < stake:
            raise BettingError(
                "Insufficient balance. Please deposit funds to place this bet.",
                code="insufficient_balance",
            )

        ticket = BetTicket.objects.create(
            account=account,
            booking_code=booking_code,
            stake=stake,
            total_odds=total_odds,
            possible_win=possible_win,
            status="open",
        )

        legs = []
        for raw in selections:
            item = normalize_bet_selection(raw)
            market_key = item["market_key"]
            if market_key not in SUPPORTED_MARKET_KEYS:
                raise BettingError(
                    f"Unsupported market: {item.get('market') or market_key}.",
                    code="unsupported_market",
                )
            legs.append(
                BetLeg(
                    ticket=ticket,
                    match_id=item["match_id"],
                    match_label=item["match_label"],
                    market_label=item.get("market") or item.get("market_label") or "1X2",
                    selection_label=item["pick"],
                    selection_key=item.get("selection_key") or "",
                    pick_side=item.get("pick_side") or "",
                    market_key=market_key,
                    odd=Decimal(str(item["odd"])).quantize(Decimal("0.01")),
                )
            )
        BetLeg.objects.bulk_create(legs)

        AccountTransaction.objects.create(
            account=account,
            tx_type=TransactionType.BET_STAKE,
            status=TransactionStatus.COMPLETED,
            amount=stake,
            fee=Decimal("0"),
            net_amount=stake,
            reference=reference,
            note=f"Bet stake for booking code {booking_code}",
            processed_at=timezone.now(),
        )

        account.current_balance = (account.current_balance - stake).quantize(Decimal("0.01"))
        account.save(update_fields=["current_balance", "updated_at"])

    return {
        "id": ticket.pk,
        "booking_code": booking_code,
        "stake": stake,
        "total_odds": total_odds,
        "possible_win": possible_win,
        "status": ticket.status,
        "placed_at": ticket.placed_at,
        "new_balance": account.current_balance,
        "legs": [
            {
                "id": f"{leg.match_id}-{leg.market_key or '1x2'}-{leg.selection_label}".lower(),
                "match_id": leg.match_id,
                "match_label": leg.match_label,
                "pick": leg.selection_label,
                "pick_side": leg.pick_side or None,
                "odd": float(leg.odd),
                "market": leg.market_label,
                "market_key": leg.market_key or None,
                "started": False,
            }
            for leg in legs
        ],
    }


def apply_bet_settlement(ticket: BetTicket) -> BetTicket:
    if ticket.settled_at is not None:
        return ticket

    if ticket.status == BetStatus.OPEN:
        return ticket

    with transaction.atomic():
        ticket = BetTicket.objects.select_for_update().get(pk=ticket.pk)
        if ticket.settled_at is not None:
            return ticket

        account = MyAccount.objects.select_for_update().get(pk=ticket.account_id)
        payout = Decimal("0")
        credit_balance = Decimal("0")
        tx_type = None
        note = ""

        if ticket.status == BetStatus.WON:
            payout = ticket.possible_win.quantize(Decimal("0.01"))
            credit_balance = payout
            tx_type = TransactionType.BET_WIN
            note = f"Bet win for booking code {ticket.booking_code}"
        elif ticket.status == BetStatus.VOID:
            payout = ticket.stake.quantize(Decimal("0.01"))
            credit_balance = payout
            tx_type = TransactionType.REFUND
            note = f"Void bet refund for booking code {ticket.booking_code}"
        elif ticket.status == BetStatus.CASHED_OUT:
            payout = (ticket.payout or Decimal("0")).quantize(Decimal("0.01"))
            credit_balance = payout
            tx_type = TransactionType.BET_WIN
            note = f"Cash out for booking code {ticket.booking_code}"
        else:
            payout = Decimal("0")

        if credit_balance > 0 and tx_type:
            AccountTransaction.objects.create(
                account=account,
                tx_type=tx_type,
                status=TransactionStatus.COMPLETED,
                amount=credit_balance,
                fee=Decimal("0"),
                net_amount=credit_balance,
                reference=f"WIN-{ticket.booking_code}",
                note=note,
                processed_at=timezone.now(),
            )
            account.current_balance = (account.current_balance + credit_balance).quantize(Decimal("0.01"))
            account.save(update_fields=["current_balance", "updated_at"])

        ticket.payout = payout
        ticket.settled_at = timezone.now()
        ticket.save(update_fields=["payout", "settled_at"])

    return ticket


def load_booking_slip(code: str) -> dict:
    normalized = (code or "").strip().upper()
    if not normalized:
        raise BettingError("Enter a booking code.", code="invalid_code")

    try:
        ticket = BetTicket.objects.prefetch_related("legs").get(booking_code=normalized)
    except BetTicket.DoesNotExist as exc:
        raise BettingError("Booking code not found.", code="not_found") from exc

    return {
        "booking_code": ticket.booking_code,
        "stake": ticket.stake,
        "selections": [
            {
                "id": f"{leg.match_id}-{leg.market_key or '1x2'}-{leg.selection_label}".lower(),
                "match_id": leg.match_id,
                "match_label": leg.match_label,
                "pick": leg.selection_label,
                "pick_side": leg.pick_side or None,
                "odd": float(leg.odd),
                "market": leg.market_label,
                "market_key": leg.market_key or None,
                "selection_key": leg.selection_key or None,
                "started": False,
            }
            for leg in ticket.legs.all()
        ],
    }
