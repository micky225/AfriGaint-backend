from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from backend.accounts.deposit_rules import calculate_deposit_credit, get_min_deposit
from backend.accounts.models import Currency, MyAccount
from backend.accounts.models import BetTicket
from backend.accounts.services.betting import BettingError, place_bet
from backend.accounts.models import PayoutSetting, PayoutType
from backend.accounts.services.deposit import DepositError, process_deposit
from backend.accounts.services.withdrawal import WithdrawalError, process_withdrawal
from backend.accounts.validators import validate_password_length

User = get_user_model()


class DepositRulesTests(TestCase):
    def test_minimum_deposit_amounts(self):
        self.assertEqual(get_min_deposit(Currency.GHS), Decimal("400"))
        self.assertEqual(get_min_deposit(Currency.NGN), Decimal("3000"))

    def test_bonus_only_at_or_above_threshold(self):
        bonus, total = calculate_deposit_credit(Decimal("2999.99"), Currency.GHS)
        self.assertEqual(bonus, Decimal("0"))
        self.assertEqual(total, Decimal("2999.99"))

        bonus, total = calculate_deposit_credit(Decimal("3000"), Currency.GHS)
        self.assertEqual(bonus, Decimal("1500.00"))
        self.assertEqual(total, Decimal("4500.00"))

        bonus, total = calculate_deposit_credit(Decimal("300000"), Currency.NGN)
        self.assertEqual(bonus, Decimal("150000.00"))
        self.assertEqual(total, Decimal("450000.00"))


class DepositServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+233500000001", password="password12345")
        self.account = self.user.my_account

    def test_rejects_below_minimum(self):
        with self.assertRaises(DepositError):
            process_deposit(self.account, Decimal("100"))

    def test_credits_balance_with_bonus(self):
        result = process_deposit(self.account, Decimal("3000"))
        self.account.refresh_from_db()
        self.assertEqual(result["bonus_amount"], Decimal("1500.00"))
        self.assertEqual(result["total_credited"], Decimal("4500.00"))
        self.assertEqual(self.account.current_balance, Decimal("4500.00"))


class PasswordValidationTests(TestCase):
    def test_password_must_be_more_than_eight_characters(self):
        with self.assertRaises(Exception):
            validate_password_length("12345678")

        validate_password_length("123456789")


class BetSettlementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+233500000020", password="password12345")
        self.account = self.user.my_account
        self.account.current_balance = Decimal("1000.00")
        self.account.save(update_fields=["current_balance", "updated_at"])
        self.selections = [
            {
                "match_id": "match-a",
                "match_label": "Team A vs Team B",
                "pick": "Home",
                "pick_side": "home",
                "odd": Decimal("2.00"),
                "market": "1X2",
            }
        ]

    def test_winning_bet_credits_balance_once(self):
        result = place_bet(self.account, Decimal("100.00"), self.selections)
        ticket = BetTicket.objects.get(pk=result["id"])
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal("900.00"))

        ticket.status = "won"
        ticket.save(update_fields=["status"])
        self.account.refresh_from_db()
        ticket.refresh_from_db()
        self.assertEqual(ticket.payout, Decimal("200.00"))
        self.assertEqual(self.account.current_balance, Decimal("1100.00"))

        ticket.status = "lost"
        ticket.save(update_fields=["status"])
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal("1100.00"))


class BettingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+233500000010", password="password12345")
        self.token, _ = Token.objects.get_or_create(user=self.user)
        self.account = self.user.my_account
        self.account.current_balance = Decimal("1000.00")
        self.account.save(update_fields=["current_balance", "updated_at"])

    def test_place_bet_and_load_booking_code(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        response = self.client.post(
            "/api/auth/account/bets/",
            {
                "stake": "100.00",
                "selections": [
                    {
                        "id": "match-a-home",
                        "match_id": "match-a",
                        "match_label": "Team A vs Team B",
                        "pick": "Home",
                        "pick_side": "home",
                        "odd": "2.00",
                        "market": "1X2",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        code = response.data["ticket"]["booking_code"]
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal("900.00"))

        load_response = self.client.get(f"/api/auth/booking/{code}/")
        self.assertEqual(load_response.status_code, 200)
        self.assertEqual(len(load_response.data["selections"]), 1)

    def test_rejects_insufficient_balance(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        response = self.client.post(
            "/api/auth/account/bets/",
            {
                "stake": "5000.00",
                "selections": [
                    {
                        "id": "match-a-home",
                        "match_id": "match-a",
                        "match_label": "Team A vs Team B",
                        "pick": "Home",
                        "odd": "2.00",
                        "market": "1X2",
                    }
                ],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "insufficient_balance")


class DepositApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(phone="+233500000002", password="password12345")
        self.token, _ = Token.objects.get_or_create(user=self.user)
        self.account = self.user.my_account

    def test_create_deposit_endpoint(self):
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")
        response = self.client.post(
            "/api/auth/account/deposits/",
            {"amount": "3000.00", "method": "momo"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.data["deposit"]["bonus_amount"]), Decimal("1500.00"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, Decimal("4500.00"))

    def test_rejects_short_password_on_register(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "country_code": "+233",
                "phone": "500000003",
                "first_name": "Test",
                "last_name": "User",
                "email": "test@example.com",
                "password": "short12",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    def test_sets_naira_currency_for_nigeria(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "country_code": "+234",
                "phone": "8012345678",
                "first_name": "Naira",
                "last_name": "User",
                "email": "naira@example.com",
                "password": "password12345",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(phone="+2348012345678")
        self.assertEqual(user.my_account.currency, Currency.NGN)


class WithdrawalGateTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+233500000050", password="password12345")
        self.account = self.user.my_account
        self.payout = PayoutSetting.objects.create(
            account=self.account,
            payout_type=PayoutType.MOMO,
            account_name="Test User",
            account_number="0244000000",
            provider_name="MTN",
            is_default=True,
        )

    def test_three_deposit_tiers_then_withdraw(self):
        process_deposit(self.account, Decimal("400"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.withdrawal_deposit_count, 1)

        balance_after_first = self.account.current_balance
        locked_result = process_withdrawal(self.account, Decimal("100"), payout_setting=self.payout)
        self.assertTrue(locked_result["locked"])
        self.assertEqual(locked_result["withdrawal_prompt"]["code"], "need_deposit_2")
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, balance_after_first - Decimal("100"))
        self.assertEqual(self.account.locked_balance, Decimal("100"))

        with self.assertRaises(BettingError):
            place_bet(
                self.account,
                self.account.current_balance + Decimal("1"),
                [
                    {
                        "match_id": "match-lock-test",
                        "match_label": "A vs B",
                        "pick": "Home",
                        "odd": Decimal("2.00"),
                        "market": "1X2",
                        "market_key": "match_result",
                    }
                ],
            )

        process_deposit(self.account, Decimal("1000"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.withdrawal_deposit_count, 2)

        balance_before_second_lock = self.account.current_balance
        second_lock = process_withdrawal(self.account, Decimal("150"), payout_setting=self.payout)
        self.assertTrue(second_lock["locked"])
        self.assertEqual(second_lock["withdrawal_prompt"]["code"], "need_deposit_3")
        self.account.refresh_from_db()
        self.assertEqual(self.account.current_balance, balance_before_second_lock - Decimal("150"))
        self.assertEqual(self.account.locked_balance, Decimal("250"))

        process_deposit(self.account, Decimal("2000"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.withdrawal_deposit_count, 3)

        balance_before = self.account.current_balance
        locked_before = self.account.locked_balance
        self.assertEqual(locked_before, Decimal("250"))

        result = process_withdrawal(self.account, Decimal("500"), payout_setting=self.payout)
        self.account.refresh_from_db()
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["pending_delivery_hours"], 72)
        self.assertEqual(result["requested_amount"], Decimal("500"))
        self.assertEqual(result["pending_included"], Decimal("250"))
        self.assertEqual(result["amount"], Decimal("750"))
        self.assertEqual(result["locked_balance"], Decimal("0"))
        self.assertEqual(self.account.current_balance, balance_before - Decimal("500"))
        self.assertEqual(self.account.locked_balance, Decimal("0"))

    def test_total_pending_withdrawal_after_merge(self):
        from backend.accounts.services.withdrawal import get_total_pending_withdrawal

        process_deposit(self.account, Decimal("400"))
        process_withdrawal(self.account, Decimal("100"), payout_setting=self.payout)
        process_deposit(self.account, Decimal("1000"))
        process_withdrawal(self.account, Decimal("150"), payout_setting=self.payout)
        process_deposit(self.account, Decimal("2000"))
        self.account.refresh_from_db()
        process_withdrawal(self.account, Decimal("500"), payout_setting=self.payout)
        self.account.refresh_from_db()

        self.assertEqual(get_total_pending_withdrawal(self.account), Decimal("750"))


class PayoutSettingsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(phone="+233500000099", password="password12345")
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {self.token.key}")

    def test_save_momo_payout_details(self):
        response = self.client.post(
            "/api/auth/account/payout-settings/",
            {
                "payout_type": "momo",
                "network": "mtn",
                "account_number": "0244123456",
                "account_name": "Test User",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["payout_setting"]["network"], "mtn")
        self.assertTrue(response.data["payout_setting"]["is_default"])

        list_response = self.client.get("/api/auth/account/payout-settings/")
        self.assertEqual(len(list_response.data), 1)

    def test_rejects_momo_without_network(self):
        response = self.client.post(
            "/api/auth/account/payout-settings/",
            {
                "payout_type": "momo",
                "account_number": "0244123456",
                "account_name": "Test User",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("network", response.data)

    def test_updates_existing_bank_payout(self):
        self.client.post(
            "/api/auth/account/payout-settings/",
            {
                "payout_type": "bank",
                "provider_name": "GCB Bank",
                "account_number": "1234567890",
                "account_name": "Test User",
            },
            format="json",
        )
        response = self.client.post(
            "/api/auth/account/payout-settings/",
            {
                "payout_type": "bank",
                "provider_name": "Ecobank",
                "account_number": "9876543210",
                "account_name": "Test User",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(PayoutSetting.objects.filter(account=self.user.my_account).count(), 1)
        self.assertEqual(response.data["payout_setting"]["provider_name"], "Ecobank")
