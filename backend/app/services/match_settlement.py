from decimal import Decimal

from django.utils import timezone

from backend.accounts.models import BetLeg, BetStatus, BetTicket, LegStatus
from backend.accounts.services.betting import apply_bet_settlement
from backend.app.models import Match, MatchStatus
from backend.app.services.market_resolver import infer_market_key, resolve_leg_outcome


def _final_score(match: Match) -> tuple[int, int]:
    home = match.outcome_home_score if match.outcome_home_score is not None else match.home_score
    away = match.outcome_away_score if match.outcome_away_score is not None else match.away_score
    return home, away


def _live_score(match: Match) -> tuple[int, int]:
    return match.home_score, match.away_score


def _outcome_to_leg_status(outcome: str) -> LegStatus | None:
    if outcome == "won":
        return LegStatus.WON
    if outcome == "lost":
        return LegStatus.LOST
    if outcome == "void":
        return LegStatus.VOID
    return None


def _resolve_leg(leg: BetLeg, home: int, away: int, *, match_finished: bool) -> str:
    return resolve_leg_outcome(
        market_key=leg.market_key or infer_market_key("", leg.market_label),
        market_label=leg.market_label,
        selection_key=leg.selection_key,
        selection_label=leg.selection_label,
        home_score=home,
        away_score=away,
        match_finished=match_finished,
    )


def _ticket_status_from_legs(legs: list[BetLeg]) -> str:
    if any(leg.status == LegStatus.LOST for leg in legs):
        return BetStatus.LOST
    if any(leg.status == LegStatus.PENDING for leg in legs):
        return BetStatus.OPEN
    active = [leg for leg in legs if leg.status != LegStatus.VOID]
    if not active:
        return BetStatus.VOID
    if all(leg.status == LegStatus.WON for leg in active):
        return BetStatus.WON
    return BetStatus.OPEN


def _recalculate_possible_win(ticket: BetTicket, legs: list[BetLeg]) -> Decimal:
    total = Decimal("1")
    for leg in legs:
        if leg.status == LegStatus.VOID:
            continue
        if leg.status == LegStatus.WON:
            total *= leg.odd
    return (ticket.stake * total).quantize(Decimal("0.01"))


def _apply_ticket_status(ticket: BetTicket, *, now) -> bool:
    legs = list(ticket.legs.all())
    new_status = _ticket_status_from_legs(legs)
    if new_status == BetStatus.OPEN:
        return False

    old_status = ticket.status
    if new_status == old_status and ticket.settled_at is not None:
        return False

    if new_status == BetStatus.WON:
        ticket.possible_win = _recalculate_possible_win(ticket, legs)
        ticket.status = BetStatus.WON
        ticket.settled_at = None
        ticket.payout = Decimal("0")
        ticket.save(update_fields=["status", "possible_win", "settled_at", "payout"])
        apply_bet_settlement(ticket)
        return True

    if new_status == BetStatus.VOID:
        ticket.status = BetStatus.VOID
        ticket.payout = Decimal("0")
        ticket.settled_at = None
        ticket.save(update_fields=["status", "payout", "settled_at"])
        apply_bet_settlement(ticket)
        return True

    ticket.status = BetStatus.LOST
    ticket.payout = Decimal("0")
    if ticket.settled_at is None:
        ticket.settled_at = now
    ticket.save(update_fields=["status", "payout", "settled_at"])
    return old_status != BetStatus.LOST


def _settle_legs_for_match(match: Match, *, match_finished: bool) -> set[int]:
    home, away = _final_score(match) if match_finished else _live_score(match)
    now = timezone.now()
    ticket_ids: set[int] = set()

    legs = BetLeg.objects.filter(match_id=match.event_id, status=LegStatus.PENDING).select_related(
        "ticket"
    )
    for leg in legs:
        outcome = _resolve_leg(leg, home, away, match_finished=match_finished)
        new_status = _outcome_to_leg_status(outcome)
        if new_status is None:
            continue
        leg.status = new_status
        leg.settled_at = now
        leg.save(update_fields=["status", "settled_at"])
        ticket_ids.add(leg.ticket_id)

    return ticket_ids


def _update_open_tickets(ticket_ids: set[int]) -> int:
    if not ticket_ids:
        return 0

    now = timezone.now()
    updated = 0
    for ticket in BetTicket.objects.filter(id__in=ticket_ids, status=BetStatus.OPEN).prefetch_related(
        "legs"
    ):
        if _apply_ticket_status(ticket, now=now):
            updated += 1
        elif _ticket_status_from_legs(list(ticket.legs.all())) == BetStatus.LOST:
            if ticket.status == BetStatus.OPEN:
                ticket.status = BetStatus.LOST
                ticket.payout = Decimal("0")
                ticket.settled_at = now
                ticket.save(update_fields=["status", "payout", "settled_at"])
                updated += 1
    return updated


def settle_live_legs_for_match(match: Match) -> int:
    """Settle legs as soon as their outcome is certain during a live match."""
    if match.status != MatchStatus.LIVE:
        return 0
    ticket_ids = _settle_legs_for_match(match, match_finished=False)
    return _update_open_tickets(ticket_ids)


def settle_bets_for_match(match: Match) -> int:
    """Resolve all remaining legs when a match finishes."""
    ticket_ids = _settle_legs_for_match(match, match_finished=True)
    return _update_open_tickets(ticket_ids)


def resettle_bets_for_match(match: Match) -> int:
    """Re-evaluate all legs for a match — fixes tickets settled with outdated rules."""
    match_finished = match.status == MatchStatus.FINISHED
    home, away = _final_score(match) if match_finished else _live_score(match)
    now = timezone.now()
    ticket_ids: set[int] = set()

    for leg in BetLeg.objects.filter(match_id=match.event_id).select_related("ticket"):
        outcome = _resolve_leg(leg, home, away, match_finished=match_finished)
        new_status = _outcome_to_leg_status(outcome) or LegStatus.PENDING
        if leg.status != new_status:
            leg.status = new_status
            leg.settled_at = now if new_status != LegStatus.PENDING else None
            leg.save(update_fields=["status", "settled_at"])
        ticket_ids.add(leg.ticket_id)

    fixed = 0
    for ticket in BetTicket.objects.filter(id__in=ticket_ids).prefetch_related("legs"):
        if _apply_ticket_status(ticket, now=now):
            fixed += 1
    return fixed
