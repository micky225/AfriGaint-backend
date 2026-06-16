from django.core.cache import cache
from django.db.models import Prefetch, Q
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from backend.app.models import BettingMarket, MarketSelection, Match, MatchStatus
from backend.app.serializers import (
    serialize_live_match,
    serialize_match_detail,
    serialize_upcoming_match,
)
from backend.app.services.match_simulation import tick_scripted_matches


class OddsRateThrottle(AnonRateThrottle):
    rate = "600/minute"


class PublicOddsAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [OddsRateThrottle]


def tick_scripted_matches_throttled() -> None:
    if cache.add("odds:tick_scripted_matches", "1", timeout=3):
        tick_scripted_matches()


def published_match_queryset():
    return (
        Match.objects.filter(is_published=True)
        .select_related("league", "home_team", "away_team")
        .prefetch_related(
            Prefetch("goal_events"),
            Prefetch(
                "markets",
                queryset=BettingMarket.objects.filter(is_active=True).prefetch_related(
                    Prefetch("selections", queryset=MarketSelection.objects.filter(is_active=True))
                ),
            ),
        )
    )


def upcoming_matches_queryset(sport: str = ""):
    qs = published_match_queryset().filter(
        Q(status=MatchStatus.SCHEDULED, commence_at__gte=timezone.now())
    )
    if sport:
        qs = qs.filter(sport_key__icontains=sport)
    return qs


class UpcomingMatchesView(PublicOddsAPIView):
    def get(self, request):
        tick_scripted_matches_throttled()

        sport = (request.query_params.get("sport") or "").strip()
        try:
            limit = min(int(request.query_params.get("limit", 100)), 500)
        except (TypeError, ValueError):
            limit = 100

        qs = upcoming_matches_queryset(sport)
        matches = qs.order_by("commence_at")[:limit]
        return Response([serialize_upcoming_match(match) for match in matches])


class LiveMatchesView(PublicOddsAPIView):
    def get(self, request):
        tick_scripted_matches_throttled()

        matches = (
            published_match_queryset()
            .filter(status=MatchStatus.LIVE)
            .order_by("kicked_off_at")
        )
        return Response([serialize_live_match(match) for match in matches])


class MatchDetailView(PublicOddsAPIView):
    def get(self, request, event_id):
        tick_scripted_matches_throttled()

        try:
            match = published_match_queryset().get(event_id=event_id)
        except Match.DoesNotExist:
            return Response({"detail": "Match not found."}, status=404)
        return Response(serialize_match_detail(match))


class LeagueListView(PublicOddsAPIView):
    def get(self, request):
        tick_scripted_matches_throttled()

        sport = (request.query_params.get("sport") or "").strip()
        leagues = {}
        for match in upcoming_matches_queryset(sport):
            key = (match.league.name, match.sport_key)
            if key not in leagues:
                leagues[key] = {
                    "league": match.league.name,
                    "sportKey": match.sport_key,
                    "matches": 0,
                }
            leagues[key]["matches"] += 1

        payload = sorted(leagues.values(), key=lambda item: (-item["matches"], item["league"]))
        return Response(payload)


class TopMatchesView(PublicOddsAPIView):
    def get(self, request):
        tick_scripted_matches_throttled()

        sport = (request.query_params.get("sport") or "").strip()
        try:
            limit = min(int(request.query_params.get("limit", 20)), 100)
        except (TypeError, ValueError):
            limit = 20

        matches = upcoming_matches_queryset(sport).order_by("commence_at", "-created_at")[:limit]
        return Response([serialize_upcoming_match(match) for match in matches])


class MatchSearchView(PublicOddsAPIView):
    def get(self, request):
        tick_scripted_matches_throttled()

        query = (request.query_params.get("q") or "").strip()
        sport = (request.query_params.get("sport") or "").strip()
        try:
            limit = min(int(request.query_params.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50

        if not query:
            return Response([])

        matches = (
            upcoming_matches_queryset(sport)
            .filter(
                Q(home_team__name__icontains=query)
                | Q(away_team__name__icontains=query)
                | Q(league__name__icontains=query)
                | Q(event_id__icontains=query)
            )
            .order_by("commence_at", "-created_at")[:limit]
        )
        return Response([serialize_upcoming_match(match) for match in matches])
