from django.core.management.base import BaseCommand, CommandError

from backend.app.models import Match
from backend.app.services.match_settlement import resettle_bets_for_match


class Command(BaseCommand):
    help = "Re-evaluate bet legs for a finished match (e.g. after settlement rule fixes)."

    def add_arguments(self, parser):
        parser.add_argument("event_id", type=str, help="Match event_id")

    def handle(self, *args, **options):
        event_id = options["event_id"]
        try:
            match = Match.objects.get(event_id=event_id)
        except Match.DoesNotExist as exc:
            raise CommandError(f"Match not found: {event_id}") from exc

        fixed = resettle_bets_for_match(match)
        self.stdout.write(
            self.style.SUCCESS(
                f"Resettled bets for {match.home_team} vs {match.away_team} — {fixed} ticket(s) updated."
            )
        )
