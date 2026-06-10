from django.db.models import Prefetch, Q
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from backend.app.models import BettingMarket, MarketSelection, Match, MatchStatus
from backend.app.serializers import (
    serialize_live_match,
    serialize_match_detail,
    serialize_upcoming_match,
)
from backend.app.services.match_simulation import tick_scripted_matches


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


class UpcomingMatchesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        tick_scripted_matches()

        sport = (request.query_params.get("sport") or "").strip()
        try:
            limit = min(int(request.query_params.get("limit", 100)), 500)
        except (TypeError, ValueError):
            limit = 100

        qs = published_match_queryset().filter(
            Q(status=MatchStatus.SCHEDULED, commence_at__gte=timezone.now())
        )

        if sport:
            qs = qs.filter(sport_key__icontains=sport)

        matches = qs.order_by("commence_at")[:limit]
        return Response([serialize_upcoming_match(match) for match in matches])


class LiveMatchesView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        tick_scripted_matches()

        matches = (
            published_match_queryset()
            .filter(status=MatchStatus.LIVE)
            .order_by("kicked_off_at")
        )
        return Response([serialize_live_match(match) for match in matches])


class MatchDetailView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, event_id):
        tick_scripted_matches()

        try:
            match = published_match_queryset().get(event_id=event_id)
        except Match.DoesNotExist:
            return Response({"detail": "Match not found."}, status=404)
        return Response(serialize_match_detail(match))
