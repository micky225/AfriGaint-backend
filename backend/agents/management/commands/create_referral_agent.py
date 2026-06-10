from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from backend.agents.models import AgentType, ReferralAgent

User = get_user_model()


class Command(BaseCommand):
    help = "Create a referral agent account with login credentials and referral code."

    def add_arguments(self, parser):
        parser.add_argument("--phone", required=True, help="Agent login phone, e.g. +233501234567")
        parser.add_argument("--password", required=True, help="Agent login password")
        parser.add_argument("--code", help="Referral code (auto-generated if omitted)")
        parser.add_argument(
            "--type",
            choices=[choice[0] for choice in AgentType.choices],
            default=AgentType.AGENT,
        )
        parser.add_argument("--name", default="", help="Display name on dashboard")
        parser.add_argument("--staff", action="store_true", help="Grant Django staff access")

    def handle(self, *args, **options):
        phone = options["phone"].strip()
        if User.objects.filter(phone=phone).exists():
            raise CommandError(f"User with phone {phone} already exists.")

        user = User.objects.create_user(
            phone=phone,
            password=options["password"],
            is_staff=options["staff"] or options["type"] == AgentType.SUPER,
        )
        code = options["code"] or ReferralAgent.generate_code()
        if ReferralAgent.objects.filter(referral_code=code).exists():
            raise CommandError(f"Referral code {code} is already in use.")

        agent = ReferralAgent.objects.create(
            user=user,
            referral_code=code,
            agent_type=options["type"],
            display_name=options["name"],
            phone=phone,
        )
        self.stdout.write(self.style.SUCCESS(f"Created agent {agent.display_name or phone}"))
        self.stdout.write(f"Referral code: {agent.referral_code}")
        self.stdout.write(f"Login phone: {phone}")
