from django.core.management.base import BaseCommand

from backend.app.services.match_simulation import tick_scripted_matches


class Command(BaseCommand):
    help = "Advance scripted local matches (live kickoff, goals, full-time settlement)"

    def handle(self, *args, **options):
        updated = tick_scripted_matches()
        self.stdout.write(self.style.SUCCESS(f"Updated {updated} scripted match(es)."))
