from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from backend.app.models import GoalSide, Match, MatchGoalEvent, MatchStatus
from backend.app.services.match_settlement import settle_bets_for_match, settle_live_legs_for_match


def tick_scripted_matches() -> int:
    """Advance scripted local matches based on current time. Returns matches updated."""
    now = timezone.now()
    updated = 0

    matches = (
        Match.objects.filter(is_published=True, is_scripted=True)
        .exclude(status__in=[MatchStatus.FINISHED, MatchStatus.CANCELLED, MatchStatus.POSTPONED])
        .prefetch_related("goal_events")
    )

    for match in matches:
        changed = False
        if match.status == MatchStatus.SCHEDULED and match.commence_at <= now:
            _start_live(match, now)
            changed = True

        if match.status == MatchStatus.LIVE:
            if _apply_due_goals(match, now):
                changed = True
            if _should_finish(match, now):
                _finish_match(match, now)
                changed = True

        if changed:
            updated += 1

    return updated


def _start_live(match: Match, now) -> None:
    match.status = MatchStatus.LIVE
    match.is_live = True
    match.kicked_off_at = now
    match.home_score = 0
    match.away_score = 0
    match.odds_locked = True
    match.save(
        update_fields=[
            "status",
            "is_live",
            "kicked_off_at",
            "home_score",
            "away_score",
            "odds_locked",
            "updated_at",
        ]
    )


def _apply_due_goals(match: Match, now) -> bool:
    changed = False
    due_events = match.goal_events.filter(is_applied=False, appears_at__lte=now).order_by(
        "appears_at", "sort_order", "id"
    )

    for event in due_events:
        if event.team_side == GoalSide.HOME:
            match.home_score += 1
        else:
            match.away_score += 1
        event.is_applied = True
        event.save(update_fields=["is_applied"])
        changed = True

    if changed:
        match.save(update_fields=["home_score", "away_score", "updated_at"])
        settle_live_legs_for_match(match)

    return changed


def _should_finish(match: Match, now) -> bool:
    if not match.kicked_off_at:
        return False

    if match.outcome_home_score is not None and match.outcome_away_score is not None:
        pending_goals = match.goal_events.filter(is_applied=False).exists()
        if pending_goals:
            return False

    end_at = match.kicked_off_at + timedelta(minutes=match.live_duration_minutes)
    return now >= end_at


@transaction.atomic
def _finish_match(match: Match, now) -> None:
    match = Match.objects.select_for_update().get(pk=match.pk)

    if match.outcome_home_score is not None:
        match.home_score = match.outcome_home_score
    if match.outcome_away_score is not None:
        match.away_score = match.outcome_away_score

    match.status = MatchStatus.FINISHED
    match.is_live = False
    match.finished_at = now
    match.odds_locked = True
    match.save(
        update_fields=[
            "home_score",
            "away_score",
            "status",
            "is_live",
            "finished_at",
            "odds_locked",
            "updated_at",
        ]
    )

    settle_bets_for_match(match)


def validate_goal_schedule(match: Match) -> list[str]:
    """Return validation errors for scripted outcome vs scheduled goals."""
    errors: list[str] = []
    if match.outcome_home_score is None or match.outcome_away_score is None:
        errors.append("Set outcome home and away scores for scripted settlement.")
        return errors

    home_goals = match.goal_events.filter(team_side=GoalSide.HOME).count()
    away_goals = match.goal_events.filter(team_side=GoalSide.AWAY).count()

    if home_goals != match.outcome_home_score:
        errors.append(
            f"Home goal events ({home_goals}) must match outcome home score ({match.outcome_home_score})."
        )
    if away_goals != match.outcome_away_score:
        errors.append(
            f"Away goal events ({away_goals}) must match outcome away score ({match.outcome_away_score})."
        )

    return errors
