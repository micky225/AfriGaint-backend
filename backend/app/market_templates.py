"""Predefined betting market templates for quick match setup in admin."""

MARKET_TEMPLATES = {
    "match_result": {
        "key": "match_result",
        "label": "Match Result",
        "group": "MAIN",
        "selections": {
            "home": "Home",
            "draw": "Draw",
            "away": "Away",
        },
    },
    "double_chance": {
        "key": "double_chance",
        "label": "Double Chance",
        "group": "MAIN",
        "selections": {
            "home_or_draw": "1 or X",
            "home_or_away": "1 or 2",
            "draw_or_away": "X or 2",
        },
    },
    "both_teams_to_score": {
        "key": "both_teams_to_score",
        "label": "Both Teams To Score",
        "group": "GOALS",
        "selections": {
            "yes": "Yes",
            "no": "No",
        },
    },
    "over_under_1_5": {
        "key": "over_under_1_5",
        "label": "Over/Under 1.5",
        "group": "GOALS",
        "selections": {
            "over_1_5": "Over 1.5",
            "under_1_5": "Under 1.5",
        },
    },
    "over_under_2_5": {
        "key": "over_under_2_5",
        "label": "Over/Under 2.5",
        "group": "GOALS",
        "selections": {
            "over_2_5": "Over 2.5",
            "under_2_5": "Under 2.5",
        },
    },
    "over_under_3_5": {
        "key": "over_under_3_5",
        "label": "Over/Under 3.5",
        "group": "GOALS",
        "selections": {
            "over_3_5": "Over 3.5",
            "under_3_5": "Under 3.5",
        },
    },
    "draw_no_bet": {
        "key": "draw_no_bet",
        "label": "Draw No Bet",
        "group": "MAIN",
        "selections": {
            "home": "Home",
            "away": "Away",
        },
    },
    "correct_score": {
        "key": "correct_score",
        "label": "Correct Score",
        "group": "SPECIALS",
        "selections": {},
    },
}
