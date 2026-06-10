from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from backend.accounts.models import BetLeg, BetStatus, BetTicket, LegStatus, MyAccount
from backend.accounts.services.betting import place_bet
from backend.app.market_templates import MARKET_TEMPLATES
from backend.app.models import GoalSide, League, Match, MatchGoalEvent, Team
from backend.app.services.market_resolver import (
    SUPPORTED_MARKET_KEYS,
    infer_market_key,
    normalize_bet_selection,
    resolve_leg_outcome,
    selection_wins,
)
from backend.app.services.match_settlement import settle_live_legs_for_match
from backend.app.services.match_simulation import tick_scripted_matches


class MarketResolverTests(TestCase):
    def test_all_template_markets_are_supported(self):
        self.assertEqual(SUPPORTED_MARKET_KEYS, frozenset(MARKET_TEMPLATES.keys()))

    def test_infer_market_from_label(self):
        self.assertEqual(infer_market_key("", "Correct Score"), "correct_score")
        self.assertEqual(infer_market_key("", "1X2"), "match_result")

    def test_normalize_bet_selection_fills_keys(self):
        normalized = normalize_bet_selection(
            {"pick": "3 - 1", "market": "Correct Score", "market_key": "", "selection_key": ""}
        )
        self.assertEqual(normalized["market_key"], "correct_score")
        self.assertEqual(normalized["selection_key"], "3_1")

    def test_match_result_home_win(self):
        self.assertTrue(
            selection_wins(
                market_key="match_result",
                selection_key="",
                selection_label="Home",
                home_score=2,
                away_score=1,
            )
        )

    def test_btts_yes(self):
        self.assertTrue(
            selection_wins(
                market_key="both_teams_to_score",
                selection_key="yes",
                selection_label="Yes",
                home_score=2,
                away_score=1,
            )
        )

    def test_correct_score_wins_on_exact_result(self):
        self.assertEqual(
            resolve_leg_outcome(
                market_key="correct_score",
                selection_key="3_1",
                selection_label="3 - 1",
                home_score=3,
                away_score=1,
            ),
            "won",
        )

    def test_correct_score_from_label_only(self):
        self.assertEqual(
            resolve_leg_outcome(
                market_key="",
                market_label="Correct Score",
                selection_key="",
                selection_label="3 - 1",
                home_score=3,
                away_score=1,
            ),
            "won",
        )

    def test_draw_no_bet_void_on_draw(self):
        self.assertEqual(
            resolve_leg_outcome(
                market_key="draw_no_bet",
                selection_key="home",
                selection_label="Home",
                home_score=1,
                away_score=1,
            ),
            "void",
        )

    def test_double_chance_home_or_draw(self):
        self.assertEqual(
            resolve_leg_outcome(
                market_key="double_chance",
                selection_key="home_or_draw",
                selection_label="1 or X",
                home_score=2,
                away_score=2,
            ),
            "won",
        )

    def test_over_under_markets(self):
        cases = [
            ("over_under_1_5", "over_1_5", 2, 0, "won"),
            ("over_under_1_5", "under_1_5", 1, 0, "won"),
            ("over_under_2_5", "over_2_5", 2, 1, "won"),
            ("over_under_2_5", "under_2_5", 1, 0, "won"),
            ("over_under_3_5", "over_3_5", 2, 2, "won"),
            ("over_under_3_5", "under_3_5", 1, 1, "won"),
        ]
        for market, selection, home, away, expected in cases:
            with self.subTest(market=market, selection=selection):
                self.assertEqual(
                    resolve_leg_outcome(
                        market_key=market,
                        selection_key=selection,
                        selection_label=selection,
                        home_score=home,
                        away_score=away,
                    ),
                    expected,
                )

    def test_btts_yes_settles_early_when_both_score(self):
        self.assertEqual(
            resolve_leg_outcome(
                market_key="both_teams_to_score",
                selection_key="yes",
                selection_label="Yes",
                home_score=1,
                away_score=1,
                match_finished=False,
            ),
            "won",
        )

    def test_over_2_5_settles_early_at_three_goals(self):
        self.assertEqual(
            resolve_leg_outcome(
                market_key="over_under_2_5",
                selection_key="over_2_5",
                selection_label="Over 2.5",
                home_score=2,
                away_score=1,
                match_finished=False,
            ),
            "won",
        )


class MatchSimulationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.league = League.objects.create(name="Test League", slug="test-league", sport_key="soccer_test")
        self.home = Team.objects.create(name="Home FC", abbr="HOM")
        self.away = Team.objects.create(name="Away FC", abbr="AWY")
        self.kickoff = timezone.now() - timedelta(minutes=1)
        self.match = Match.objects.create(
            event_id="sim-test-match",
            league=self.league,
            home_team=self.home,
            away_team=self.away,
            commence_at=self.kickoff,
            sport_key="soccer_test",
            is_scripted=True,
            outcome_home_score=1,
            outcome_away_score=0,
            live_duration_minutes=2,
        )
        MatchGoalEvent.objects.create(
            match=self.match,
            team_side=GoalSide.HOME,
            match_minute=10,
            appears_at=timezone.now() - timedelta(seconds=30),
        )
        self.match.add_market_from_template("match_result", {"home": "1.50", "draw": "3.50", "away": "5.00"})

    def test_match_goes_live_and_scores(self):
        tick_scripted_matches()
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, "live")
        self.assertEqual(self.match.home_score, 1)
        self.assertTrue(self.match.odds_locked)

    def test_live_endpoint_lists_scripted_match(self):
        tick_scripted_matches()
        response = self.client.get("/api/odds/live")
        self.assertEqual(response.status_code, 200)
        ids = [item["eventId"] for item in response.data]
        self.assertIn("sim-test-match", ids)

    def test_live_payload_matches_external_feed_shape(self):
        tick_scripted_matches()
        response = self.client.get("/api/odds/live")
        payload = next(item for item in response.data if item["eventId"] == "sim-test-match")

        self.assertEqual(payload["status"], "LIVE")
        self.assertTrue(payload["isLive"])
        self.assertTrue(payload["oddsLocked"])
        self.assertEqual(payload["odds"], ["-", "-", "-"])
        self.assertIsNone(payload["finalWhistleAt"])
        self.assertIsNotNone(payload["kickedOffAt"])
        self.assertIsNotNone(payload["halftimeAt"])
        self.assertIsNotNone(payload["secondHalfStartedAt"])
        self.assertEqual(payload["homeScore"], "1")
        self.assertEqual(payload["awayScore"], "0")

    def test_winning_bet_settles_after_full_time(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(phone="+233500000099", password="password12345")
        account = user.my_account
        account.current_balance = Decimal("1000")
        account.save(update_fields=["current_balance"])

        place_bet(
            account,
            Decimal("100"),
            [
                {
                    "match_id": self.match.event_id,
                    "match_label": "Home FC vs Away FC",
                    "pick": "Home",
                    "market": "Match Result",
                    "market_key": "match_result",
                    "odd": Decimal("1.50"),
                }
            ],
        )

        tick_scripted_matches()
        self.match.refresh_from_db()
        self.match.kicked_off_at = timezone.now() - timedelta(minutes=5)
        self.match.save(update_fields=["kicked_off_at"])

        tick_scripted_matches()
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, "finished")
        self.assertEqual(self.match.home_score, 1)

        leg = BetLeg.objects.get(match_id=self.match.event_id)
        self.assertEqual(leg.status, LegStatus.WON)

        ticket = BetTicket.objects.get(pk=leg.ticket_id)
        self.assertEqual(ticket.status, BetStatus.WON)

        account.refresh_from_db()
        self.assertEqual(account.current_balance, Decimal("1050.00"))

        from rest_framework.authtoken.models import Token

        token = Token.objects.create(user=user)
        bets_response = self.client.get(
            "/api/auth/account/bets/?scope=settled",
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )
        self.assertEqual(bets_response.status_code, 200)
        leg_payload = bets_response.data[0]["legs"][0]
        self.assertEqual(leg_payload["status"], "won")
        self.assertEqual(leg_payload["match_status"], "finished")
        self.assertEqual(leg_payload["home_score"], 1)
        self.assertEqual(leg_payload["away_score"], 0)

    def test_correct_score_bet_wins_on_finish(self):
        from django.contrib.auth import get_user_model

        self.match.outcome_home_score = 3
        self.match.outcome_away_score = 1
        self.match.save(update_fields=["outcome_home_score", "outcome_away_score"])
        self.match.add_market_from_template("correct_score", {"3_1": "17.75"})

        user = get_user_model().objects.create_user(phone="+233500000100", password="password12345")
        account = user.my_account
        account.current_balance = Decimal("5000")
        account.save(update_fields=["current_balance"])

        place_bet(
            account,
            Decimal("100"),
            [
                {
                    "match_id": self.match.event_id,
                    "match_label": "Home FC vs Away FC",
                    "pick": "3 - 1",
                    "market": "Correct Score",
                    "market_key": "correct_score",
                    "selection_key": "3_1",
                    "odd": Decimal("17.75"),
                }
            ],
        )

        tick_scripted_matches()
        self.match.refresh_from_db()
        self.match.kicked_off_at = timezone.now() - timedelta(minutes=5)
        self.match.save(update_fields=["kicked_off_at"])
        tick_scripted_matches()

        leg = BetLeg.objects.get(match_id=self.match.event_id)
        self.assertEqual(leg.status, LegStatus.WON)
        ticket = BetTicket.objects.get(pk=leg.ticket_id)
        self.assertEqual(ticket.status, BetStatus.WON)

    def test_btts_yes_settles_immediately_on_second_goal(self):
        from django.contrib.auth import get_user_model

        self.match.outcome_home_score = 2
        self.match.outcome_away_score = 1
        self.match.save(update_fields=["outcome_home_score", "outcome_away_score"])
        MatchGoalEvent.objects.all().delete()
        MatchGoalEvent.objects.create(
            match=self.match,
            team_side=GoalSide.HOME,
            match_minute=10,
            appears_at=timezone.now() + timedelta(seconds=5),
        )
        MatchGoalEvent.objects.create(
            match=self.match,
            team_side=GoalSide.AWAY,
            match_minute=20,
            appears_at=timezone.now() + timedelta(seconds=10),
        )
        self.match.add_market_from_template("both_teams_to_score", {"yes": "1.85", "no": "1.95"})

        user = get_user_model().objects.create_user(phone="+233500000101", password="password12345")
        account = user.my_account
        account.current_balance = Decimal("2000")
        account.save(update_fields=["current_balance"])

        place_bet(
            account,
            Decimal("100"),
            [
                {
                    "match_id": self.match.event_id,
                    "match_label": "Home FC vs Away FC",
                    "pick": "Yes",
                    "market": "Both Teams To Score",
                    "market_key": "both_teams_to_score",
                    "selection_key": "yes",
                    "odd": Decimal("1.85"),
                }
            ],
        )

        tick_scripted_matches()
        self.match.refresh_from_db()
        self.assertEqual(self.match.status, "live")

        self.match.home_score = 1
        self.match.away_score = 1
        self.match.save(update_fields=["home_score", "away_score"])
        settle_live_legs_for_match(self.match)

        leg = BetLeg.objects.get(match_id=self.match.event_id)
        self.assertEqual(leg.status, LegStatus.WON)
        ticket = BetTicket.objects.get(pk=leg.ticket_id)
        self.assertEqual(ticket.status, BetStatus.WON)
