import uuid
from decimal import Decimal, InvalidOperation

from django.db import models
from django.utils import timezone

from backend.app.market_templates import MARKET_TEMPLATES


class League(models.Model):
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True)
    country = models.CharField(max_length=80, blank=True, default="")
    sport_key = models.CharField(max_length=80, default="soccer")
    flag_emoji = models.CharField(max_length=8, blank=True, default="🏆")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Team(models.Model):
    name = models.CharField(max_length=120, unique=True)
    abbr = models.CharField(max_length=12, blank=True, default="")
    color = models.CharField(max_length=20, blank=True, default="#f97316")
    logo_url = models.URLField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MatchStatus(models.TextChoices):
    SCHEDULED = "scheduled", "Scheduled"
    LIVE = "live", "Live"
    HALFTIME = "halftime", "Halftime"
    FINISHED = "finished", "Finished"
    POSTPONED = "postponed", "Postponed"
    CANCELLED = "cancelled", "Cancelled"


class Match(models.Model):
    event_id = models.CharField(max_length=120, unique=True, db_index=True)
    league = models.ForeignKey(League, on_delete=models.PROTECT, related_name="matches")
    home_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="home_matches")
    away_team = models.ForeignKey(Team, on_delete=models.PROTECT, related_name="away_matches")
    commence_at = models.DateTimeField()
    sport_key = models.CharField(max_length=80)
    status = models.CharField(max_length=20, choices=MatchStatus.choices, default=MatchStatus.SCHEDULED)
    is_live = models.BooleanField(default=False)
    is_published = models.BooleanField(default=True)
    is_scripted = models.BooleanField(
        default=True,
        help_text="When enabled, match goes live at kickoff using scripted goals and settles bets at full time.",
    )
    home_score = models.PositiveSmallIntegerField(default=0)
    away_score = models.PositiveSmallIntegerField(default=0)
    outcome_home_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Final home goals (scripted outcome).",
    )
    outcome_away_score = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Final away goals (scripted outcome).",
    )
    live_duration_minutes = models.PositiveSmallIntegerField(default=90)
    kicked_off_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    odds_locked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["commence_at"]
        verbose_name_plural = "matches"

    def __str__(self):
        return f"{self.home_team} vs {self.away_team}"

    def save(self, *args, **kwargs):
        if not self.event_id:
            self.event_id = f"local-{uuid.uuid4().hex[:12]}"
        if not self.sport_key and self.league_id:
            self.sport_key = self.league.sport_key
        self.is_live = self.status == MatchStatus.LIVE
        super().save(*args, **kwargs)

    @property
    def market_count(self) -> int:
        return self.markets.filter(is_active=True).count()

    def get_match_result_market(self):
        return self.markets.filter(key="match_result", is_active=True).first()

    def primary_odds_tuple(self) -> tuple[str, str, str]:
        """Return [home, draw, away] odds strings for the upcoming feed."""
        market = self.get_match_result_market()
        if not market:
            return ("-", "-", "-")

        selections = {item.key: item for item in market.selections.filter(is_active=True)}
        home = selections.get("home")
        draw = selections.get("draw")
        away = selections.get("away")

        if home and away and not draw:
            return (self._format_odd(home.odd), "-", self._format_odd(away.odd))

        return (
            self._format_odd(home.odd) if home else "-",
            self._format_odd(draw.odd) if draw else "-",
            self._format_odd(away.odd) if away else "-",
        )

    @staticmethod
    def _format_odd(value) -> str:
        if value is None:
            return "-"
        try:
            return str(Decimal(value).quantize(Decimal("0.01")))
        except (InvalidOperation, TypeError):
            return "-"

    def add_market_from_template(self, template_key: str, odds: dict[str, str | float]) -> "BettingMarket":
        template = MARKET_TEMPLATES.get(template_key)
        if not template:
            raise ValueError(f"Unknown market template: {template_key}")

        market, _ = BettingMarket.objects.get_or_create(
            match=self,
            key=template["key"],
            defaults={
                "label": template["label"],
                "group": template["group"],
            },
        )
        market.label = template["label"]
        market.group = template["group"]
        market.is_active = True
        market.save()

        label_map = template["selections"]
        for selection_key, odd_value in odds.items():
            if selection_key in label_map:
                label = label_map[selection_key]
            elif "_" in selection_key and selection_key.replace("_", "").isdigit():
                parts = selection_key.split("_")
                label = f"{parts[0]} - {parts[1]}"
            else:
                label = selection_key.replace("_", " ").title()
            MarketSelection.objects.update_or_create(
                market=market,
                key=selection_key,
                defaults={
                    "label": label,
                    "odd": Decimal(str(odd_value)),
                    "is_active": True,
                },
            )

        return market


class BettingMarket(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="markets")
    key = models.CharField(max_length=80)
    label = models.CharField(max_length=120)
    group = models.CharField(max_length=40, default="MAIN")
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = [["match", "key"]]

    def __str__(self):
        return f"{self.match.event_id} · {self.label}"


class MarketSelection(models.Model):
    market = models.ForeignKey(BettingMarket, on_delete=models.CASCADE, related_name="selections")
    key = models.CharField(max_length=80)
    label = models.CharField(max_length=120)
    odd = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["id"]
        unique_together = [["market", "key"]]

    def __str__(self):
        return f"{self.label} @ {self.odd}"


class GoalSide(models.TextChoices):
    HOME = "home", "Home"
    AWAY = "away", "Away"


class MatchGoalEvent(models.Model):
    match = models.ForeignKey(Match, on_delete=models.CASCADE, related_name="goal_events")
    team_side = models.CharField(max_length=8, choices=GoalSide.choices)
    match_minute = models.PositiveSmallIntegerField(
        help_text="Minute shown in live feed, e.g. 23 for 23'.",
    )
    appears_at = models.DateTimeField(
        help_text="Wall-clock time when this goal is revealed in the live feed.",
    )
    is_applied = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["appears_at", "sort_order", "id"]

    def __str__(self):
        side = "Home" if self.team_side == GoalSide.HOME else "Away"
        return f"{side} goal {self.match_minute}' @ {self.appears_at:%H:%M}"
