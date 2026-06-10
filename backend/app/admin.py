from django.contrib import admin
from django.utils.html import format_html

from backend.app.models import BettingMarket, League, MarketSelection, Match, MatchGoalEvent, Team
from backend.app.services.match_simulation import validate_goal_schedule


class MatchGoalEventInline(admin.TabularInline):
    model = MatchGoalEvent
    extra = 2
    fields = ["team_side", "match_minute", "appears_at", "is_applied", "sort_order"]
    ordering = ["appears_at"]


class MarketSelectionInline(admin.TabularInline):
    model = MarketSelection
    extra = 1
    fields = ["key", "label", "odd", "is_active"]


class BettingMarketInline(admin.TabularInline):
    model = BettingMarket
    extra = 0
    fields = ["key", "label", "group", "sort_order", "is_active"]
    show_change_link = True


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ["name", "country", "sport_key", "flag_emoji", "is_active", "match_count"]
    list_filter = ["is_active", "sport_key", "country"]
    search_fields = ["name", "slug", "country"]
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Matches")
    def match_count(self, obj):
        return obj.matches.count()


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ["name", "abbr", "color", "logo_preview"]
    search_fields = ["name", "abbr"]

    @admin.display(description="Logo")
    def logo_preview(self, obj):
        if not obj.logo_url:
            return "—"
        return format_html('<img src="{}" height="24" />', obj.logo_url)


@admin.register(Match)
class MatchAdmin(admin.ModelAdmin):
    list_display = [
        "event_id",
        "league",
        "home_team",
        "away_team",
        "commence_at",
        "outcome_display",
        "status",
        "is_published",
        "is_scripted",
    ]
    list_filter = ["status", "is_published", "is_scripted", "is_live", "league", "sport_key"]
    search_fields = ["event_id", "home_team__name", "away_team__name", "league__name"]
    autocomplete_fields = ["league", "home_team", "away_team"]
    readonly_fields = [
        "created_at",
        "updated_at",
        "market_count_display",
        "kicked_off_at",
        "finished_at",
        "script_validation_display",
    ]
    inlines = [MatchGoalEventInline, BettingMarketInline]
    actions = ["publish_matches", "unpublish_matches", "apply_default_markets"]

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "event_id",
                    "league",
                    "home_team",
                    "away_team",
                    "commence_at",
                    "sport_key",
                    "live_duration_minutes",
                )
            },
        ),
        (
            "Scripted outcome",
            {
                "description": (
                    "Set the final score and schedule when each goal appears. "
                    "At kickoff the match goes live; goals reveal at their times; "
                    "at full time bets settle automatically."
                ),
                "fields": (
                    "is_scripted",
                    "outcome_home_score",
                    "outcome_away_score",
                    "script_validation_display",
                ),
            },
        ),
        (
            "Live state",
            {
                "fields": (
                    "status",
                    "is_live",
                    "is_published",
                    "odds_locked",
                    "home_score",
                    "away_score",
                    "kicked_off_at",
                    "finished_at",
                )
            },
        ),
        ("Meta", {"fields": ("market_count_display", "created_at", "updated_at")}),
    )

    @admin.display(description="Outcome")
    def outcome_display(self, obj):
        if obj.outcome_home_score is None or obj.outcome_away_score is None:
            return "—"
        return f"{obj.outcome_home_score}-{obj.outcome_away_score}"

    @admin.display(description="Goal schedule check")
    def script_validation_display(self, obj):
        if not obj.pk:
            return "Save match first, then add goal events."
        errors = validate_goal_schedule(obj)
        if not errors:
            return format_html(
                '<span style="color:green;">{}</span>',
                "Valid — goals match outcome.",
            )
        return format_html('<span style="color:red;">{}</span>', " ".join(errors))

    @admin.display(description="Markets")
    def market_count_display(self, obj):
        return obj.market_count

    @admin.action(description="Publish selected matches")
    def publish_matches(self, request, queryset):
        queryset.update(is_published=True)

    @admin.action(description="Unpublish selected matches")
    def unpublish_matches(self, request, queryset):
        queryset.update(is_published=False)

    @admin.action(description="Apply default market templates (1X2, BTTS, Double Chance, O/U 2.5)")
    def apply_default_markets(self, request, queryset):
        for match in queryset:
            match.add_market_from_template("match_result", {"home": "2.00", "draw": "3.20", "away": "3.50"})
            match.add_market_from_template("both_teams_to_score", {"yes": "1.85", "no": "1.95"})
            match.add_market_from_template("double_chance", {
                "home_or_draw": "1.30",
                "home_or_away": "1.35",
                "draw_or_away": "1.65",
            })
            match.add_market_from_template("over_under_2_5", {"over_2_5": "1.90", "under_2_5": "1.90"})


@admin.register(MatchGoalEvent)
class MatchGoalEventAdmin(admin.ModelAdmin):
    list_display = ["match", "team_side", "match_minute", "appears_at", "is_applied"]
    list_filter = ["team_side", "is_applied"]
    search_fields = ["match__event_id"]


@admin.register(BettingMarket)
class BettingMarketAdmin(admin.ModelAdmin):
    list_display = ["match", "label", "key", "group", "selection_count", "is_active"]
    list_filter = ["group", "is_active"]
    search_fields = ["match__event_id", "label", "key"]
    inlines = [MarketSelectionInline]

    @admin.display(description="Selections")
    def selection_count(self, obj):
        return obj.selections.count()


@admin.register(MarketSelection)
class MarketSelectionAdmin(admin.ModelAdmin):
    list_display = ["market", "label", "key", "odd", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["market__match__event_id", "label", "key"]
