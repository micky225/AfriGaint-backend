from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from backend.accounts.services.deposit import process_deposit
from backend.agents.models import AgentType, ReferralAgent

User = get_user_model()


class ReferralAgentTests(TestCase):
    def setUp(self):
        self.agent_user = User.objects.create_user(phone="+233500000200", password="password12345")
        self.agent = ReferralAgent.objects.create(
            user=self.agent_user,
            referral_code="9705",
            agent_type=AgentType.AGENT,
            display_name="Test Admin",
        )
        self.agent_token = Token.objects.create(user=self.agent_user)
        self.client = APIClient()

        self.member_user = User.objects.create_user(
            phone="+233500000201",
            password="password12345",
            first_name="Member",
            last_name="One",
        )
        account = self.member_user.my_account
        account.referred_by_agent = self.agent
        account.referral_code_used = self.agent.referral_code
        account.save(update_fields=["referred_by_agent", "referral_code_used", "updated_at"])

    def test_agent_dashboard_scoped_to_referrals(self):
        process_deposit(self.member_user.my_account, Decimal("1000"))
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.agent_token.key}")
        response = self.client.get("/api/agents/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"]["approved_count"], 1)
        self.assertEqual(response.data["summary"]["member_count"], 1)
        self.assertEqual(response.data["summary"]["referrer_share"], "500.00")

    def test_registration_with_referral_code(self):
        client = APIClient()
        response = client.post(
            "/api/auth/register/",
            {
                "country_code": "+233",
                "phone": "500000202",
                "first_name": "New",
                "last_name": "Player",
                "email": "new@example.com",
                "password": "password12345",
                "referral_code": "9705",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(phone="+233500000202")
        self.assertEqual(user.my_account.referred_by_agent_id, self.agent.id)


class SuperAdminRosterTests(TestCase):
    def setUp(self):
        self.super_user = User.objects.create_user(phone="+233500000300", password="password12345")
        self.super_agent = ReferralAgent.objects.create(
            user=self.super_user,
            referral_code="SUPER1",
            agent_type=AgentType.SUPER,
            display_name="Super Admin",
        )
        self.super_token = Token.objects.create(user=self.super_user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.super_token.key}")

        self.agent_user = User.objects.create_user(phone="+233500000301", password="password12345")
        self.agent = ReferralAgent.objects.create(
            user=self.agent_user,
            referral_code="AGENT1",
            agent_type=AgentType.AGENT,
            display_name="Field Agent",
        )

        self.member = User.objects.create_user(phone="+233500000302", password="password12345")
        account = self.member.my_account
        account.referred_by_agent = self.agent
        account.save(update_fields=["referred_by_agent", "updated_at"])
        process_deposit(account, Decimal("500"))

    def test_super_admin_dashboard_includes_agents_roster(self):
        response = self.client.get("/api/agents/dashboard/")
        self.assertEqual(response.status_code, 200)
        roster = response.data["agents_roster"]
        self.assertEqual(len(roster), 1)
        entry = roster[0]
        self.assertEqual(entry["referral_code"], "AGENT1")
        self.assertEqual(entry["member_count"], 1)
        self.assertEqual(entry["approved_count"], 1)
        self.assertEqual(entry["approved_amount"], "500.00")
        self.assertEqual(entry["rejected_count"], 0)

    def test_regular_agent_has_no_roster(self):
        token = Token.objects.create(user=self.agent_user)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        response = self.client.get("/api/agents/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("agents_roster", response.data)
