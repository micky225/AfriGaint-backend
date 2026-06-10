from datetime import timedelta

from django.utils import timezone

from backend.app.models import GoalSide, Match, MatchStatus


def _team_payload(team) -> dict:
    return {
        "name": team.name,
        "abbr": team.abbr or team.name[:3].upper(),
        "color": team.color or "#f97316",
        "logoUrl": team.logo_url or "",
    }


def _status_label(match: Match) -> str:
    if match.status == MatchStatus.LIVE:
        return "LIVE"
    if match.status == MatchStatus.FINISHED:
        return "Finished"
    if match.status == MatchStatus.POSTPONED:
        return "Postponed"
    if match.status == MatchStatus.CANCELLED:
        return "Cancelled"
    if match.commence_at <= timezone.now():
        return "Starting soon"
    return "Scheduled"


def _odds_tuple(match: Match) -> tuple[str, str, str]:
    if match.odds_locked or match.status == MatchStatus.LIVE:
        return ("-", "-", "-")
    return match.primary_odds_tuple()


def _scaled_clock_anchors(match: Match) -> dict:
    """Map wall-clock live window to 0–90' match minutes (same model as external feed)."""
    if not match.kicked_off_at or match.status != MatchStatus.LIVE:
        return {}

    total = max(1, match.live_duration_minutes)
    ko = match.kicked_off_at
    first_half = total * 45 / 90
    ht_break = max(total * 15 / 90, 0.5)
    ht_at = ko + timedelta(minutes=first_half)
    sh_at = ht_at + timedelta(minutes=ht_break)
    return {
        "halftimeAt": ht_at.isoformat(),
        "secondHalfStartedAt": sh_at.isoformat(),
    }


def _goal_schedule(match: Match) -> list[dict]:
    schedule = []
    home = 0
    away = 0
    for event in match.goal_events.all().order_by("appears_at", "sort_order", "id"):
        if event.team_side == GoalSide.HOME:
            home += 1
        else:
            away += 1
        schedule.append(
            {
                "home": home,
                "away": away,
                "at": event.appears_at.isoformat(),
                "minute": event.match_minute,
            }
        )
    return schedule


def serialize_upcoming_match(match: Match) -> dict:
    home_odd, draw_odd, away_odd = _odds_tuple(match)
    return {
        "league": match.league.name,
        "isLive": match.is_live,
        "status": _status_label(match),
        "home": _team_payload(match.home_team),
        "away": _team_payload(match.away_team),
        "homeScore": str(match.home_score),
        "awayScore": str(match.away_score),
        "odds": [home_odd, draw_odd, away_odd],
        "oddsLocked": match.odds_locked,
        "markets": match.market_count,
        "sportKey": match.sport_key,
        "eventId": match.event_id,
        "commenceAt": match.commence_at.isoformat(),
    }


def serialize_live_match(match: Match) -> dict:
    home_odd, draw_odd, away_odd = _odds_tuple(match)
    payload = {
        "league": match.league.name,
        "isLive": True,
        "status": "LIVE",
        "home": _team_payload(match.home_team),
        "away": _team_payload(match.away_team),
        "homeScore": str(match.home_score),
        "awayScore": str(match.away_score),
        "odds": [home_odd, draw_odd, away_odd],
        "oddsLocked": True,
        "markets": match.market_count,
        "sportKey": match.sport_key,
        "eventId": match.event_id,
        "source": "local",
        "kickedOffAt": match.kicked_off_at.isoformat() if match.kicked_off_at else None,
        "finalWhistleAt": None,
        "goalSchedule": _goal_schedule(match),
    }
    payload.update(_scaled_clock_anchors(match))
    return payload


def serialize_match_detail(match: Match) -> dict:
    home_odd, draw_odd, away_odd = match.primary_odds_tuple()
    markets = []
    for market in match.markets.filter(is_active=True).prefetch_related("selections"):
        selections = [
            {
                "key": selection.key,
                "label": selection.label,
                "odd": str(selection.odd),
            }
            for selection in market.selections.filter(is_active=True)
        ]
        if selections:
            markets.append(
                {
                    "key": market.key,
                    "label": market.label,
                    "group": market.group,
                    "selections": selections,
                }
            )

    return {
        "id": match.event_id,
        "eventId": match.event_id,
        "league": match.league.name,
        "homeTeam": match.home_team.name,
        "awayTeam": match.away_team.name,
        "homeLogoUrl": match.home_team.logo_url or "",
        "awayLogoUrl": match.away_team.logo_url or "",
        "commenceAt": match.commence_at.isoformat(),
        "status": _status_label(match),
        "homeOdd": None if home_odd == "-" else home_odd,
        "drawOdd": None if draw_odd == "-" else draw_odd,
        "awayOdd": None if away_odd == "-" else away_odd,
        "sportKey": match.sport_key,
        "homeScore": match.home_score,
        "awayScore": match.away_score,
        "isLive": match.is_live,
        "markets": {
            "generatedAt": timezone.now().isoformat(),
            "markets": markets,
        },
    }
