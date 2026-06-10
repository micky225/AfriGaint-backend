from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from backend.app.models import GoalSide, League, Match, MatchGoalEvent, Team


class Command(BaseCommand):
    help = "Seed a sample upcoming match with scripted outcome and goal times"

    def handle(self, *args, **options):
        league, _ = League.objects.get_or_create(
            slug="primera-division-chile",
            defaults={
                "name": "Primera División",
                "country": "Chile",
                "sport_key": "soccer_chile_primera_division",
            },
        )
        home, _ = Team.objects.get_or_create(
            name="Universidad de Chile",
            defaults={"abbr": "UCH", "color": "#0033a0"},
        )
        away, _ = Team.objects.get_or_create(
            name="O'Higgins",
            defaults={"abbr": "OHI", "color": "#00a651"},
        )

        kickoff = timezone.now() + timedelta(minutes=2)
        match, created = Match.objects.get_or_create(
            event_id="chile-udechile-ohiggins-20260619",
            defaults={
                "league": league,
                "home_team": home,
                "away_team": away,
                "commence_at": kickoff,
                "sport_key": "soccer_chile_primera_division",
                "is_scripted": True,
                "outcome_home_score": 2,
                "outcome_away_score": 1,
                "live_duration_minutes": 5,
            },
        )

        match.commence_at = kickoff
        match.is_scripted = True
        match.outcome_home_score = 2
        match.outcome_away_score = 1
        match.live_duration_minutes = 5
        match.status = "scheduled"
        match.is_live = False
        match.home_score = 0
        match.away_score = 0
        match.odds_locked = False
        match.kicked_off_at = None
        match.finished_at = None
        match.save()

        match.goal_events.all().delete()
        goal_plan = [
            (GoalSide.HOME, 12, kickoff + timedelta(minutes=1)),
            (GoalSide.AWAY, 34, kickoff + timedelta(minutes=2)),
            (GoalSide.HOME, 78, kickoff + timedelta(minutes=3)),
        ]
        for index, (side, minute, appears_at) in enumerate(goal_plan):
            MatchGoalEvent.objects.create(
                match=match,
                team_side=side,
                match_minute=minute,
                appears_at=appears_at,
                sort_order=index,
            )

        match.add_market_from_template("match_result", {
            "home": "1.78", "draw": "3.84", "away": "4.88",
        })
        match.add_market_from_template("over_under_1_5", {
            "over_1_5": "1.47", "under_1_5": "2.82",
        })
        match.add_market_from_template("over_under_2_5", {
            "over_2_5": "2.27", "under_2_5": "1.57",
        })
        match.add_market_from_template("both_teams_to_score", {
            "yes": "2.11", "no": "1.73",
        })
        match.add_market_from_template("double_chance", {
            "home_or_draw": "1.14", "home_or_away": "1.33", "draw_or_away": "2.15",
        })

        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} scripted sample match: {match.event_id} "
                f"(kickoff in 2 min, final 2-1, 5 min live window)"
            )
        )
