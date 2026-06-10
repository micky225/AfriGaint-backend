"""Resolve whether a bet selection won based on match scores."""

from __future__ import annotations

import re
from typing import Literal

from backend.app.market_templates import MARKET_TEMPLATES

_SCORE_PAIR_RE = re.compile(r"(\d+)\s*[-:]\s*(\d+)")

LegOutcome = Literal["won", "lost", "void", "pending"]

SUPPORTED_MARKET_KEYS = frozenset(MARKET_TEMPLATES.keys())

MARKET_LABEL_ALIASES: dict[str, str] = {
    "1x2": "match_result",
    "match": "match_result",
    "match result": "match_result",
    "double chance": "double_chance",
    "both teams to score": "both_teams_to_score",
    "btts": "both_teams_to_score",
    "over/under 1.5": "over_under_1_5",
    "over/under 2.5": "over_under_2_5",
    "over/under 3.5": "over_under_3_5",
    "draw no bet": "draw_no_bet",
    "dnb": "draw_no_bet",
    "correct score": "correct_score",
}


def infer_market_key(market_key: str = "", market_label: str = "") -> str:
    key = (market_key or "").strip().lower()
    if key in {"", "1x2"}:
        key = ""
    elif key in SUPPORTED_MARKET_KEYS:
        return key

    label = (market_label or "").strip().lower()
    if label in MARKET_LABEL_ALIASES:
        return MARKET_LABEL_ALIASES[label]

    if key:
        return key
    return "match_result"


def _normalize_selection_key(market_key: str, selection_key: str, selection_label: str) -> str:
    if selection_key:
        return selection_key.strip().lower()

    label = (selection_label or "").strip().lower()
    market = infer_market_key(market_key)

    if market == "match_result":
        mapping = {
            "home": "home",
            "1": "home",
            "draw": "draw",
            "x": "draw",
            "away": "away",
            "2": "away",
        }
        return mapping.get(label, label.replace(" ", "_"))

    if market == "both_teams_to_score":
        return "yes" if label in {"yes", "y"} else "no" if label in {"no", "n"} else label

    if market == "double_chance":
        mapping = {
            "1 or x": "home_or_draw",
            "1 or 2": "home_or_away",
            "x or 2": "draw_or_away",
            "home or draw": "home_or_draw",
            "home or away": "home_or_away",
            "draw or away": "draw_or_away",
        }
        return mapping.get(label, label.replace(" ", "_"))

    if market == "draw_no_bet":
        mapping = {"home": "home", "1": "home", "away": "away", "2": "away"}
        return mapping.get(label, label.replace(" ", "_"))

    if market.startswith("over_under"):
        if "over" in label:
            return market.replace("over_under", "over_").replace("__", "_")
        if "under" in label:
            return market.replace("over_under", "under_").replace("__", "_")

    return label.replace(" ", "_")


def infer_selection_key(
    *,
    market_key: str,
    pick: str,
    pick_side: str = "",
    selection_key: str = "",
) -> str:
    if selection_key:
        return selection_key.strip().lower()

    market = infer_market_key(market_key)
    if market == "correct_score":
        parsed = _parse_correct_score("", pick)
        if parsed:
            return f"{parsed[0]}_{parsed[1]}"

    side = (pick_side or "").strip().lower()
    if side in {"home", "draw", "away"}:
        return side

    return _normalize_selection_key(market_key, "", pick)


def normalize_bet_selection(item: dict) -> dict:
    """Ensure market_key and selection_key are always stored for settlement."""
    market_key = infer_market_key(
        item.get("market_key") or "",
        item.get("market") or item.get("market_label") or "",
    )
    selection_key = infer_selection_key(
        market_key=market_key,
        pick=item.get("pick") or "",
        pick_side=item.get("pick_side") or "",
        selection_key=item.get("selection_key") or "",
    )
    return {
        **item,
        "market_key": market_key,
        "selection_key": selection_key,
    }


def _parse_correct_score(selection_key: str, selection_label: str) -> tuple[int, int] | None:
    key = (selection_key or "").strip().lower()
    if key and "_" in key:
        parts = key.split("_", 1)
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])

    for candidate in (selection_label, key.replace("_", " ")):
        if not candidate:
            continue
        match = _SCORE_PAIR_RE.search(candidate.strip())
        if match:
            return int(match.group(1)), int(match.group(2))

    return None


def _over_under_line(market: str) -> float | None:
    if market == "over_under_1_5":
        return 1.5
    if market == "over_under_2_5":
        return 2.5
    if market == "over_under_3_5":
        return 3.5
    return None


def is_leg_decided(
    *,
    market_key: str,
    market_label: str,
    selection_key: str,
    selection_label: str,
    home_score: int,
    away_score: int,
    match_finished: bool,
) -> bool:
    """Whether a leg can be settled now (full time or outcome already certain)."""
    if match_finished:
        return True

    market = infer_market_key(market_key, market_label)
    selection = _normalize_selection_key(market, selection_key, selection_label)
    total_goals = home_score + away_score
    both_scored = home_score > 0 and away_score > 0

    if market == "both_teams_to_score":
        if selection == "yes" and both_scored:
            return True
        if selection == "no" and both_scored:
            return True
        return False

    line = _over_under_line(market)
    if line is not None:
        if selection.startswith("over_") and total_goals > line:
            return True
        if selection.startswith("under_") and total_goals > line:
            return True
        return False

    return False


def resolve_leg_outcome(
    *,
    market_key: str,
    market_label: str = "",
    selection_key: str,
    selection_label: str,
    home_score: int,
    away_score: int,
    match_finished: bool = True,
) -> LegOutcome:
    market = infer_market_key(market_key, market_label)
    selection = _normalize_selection_key(market, selection_key, selection_label)

    if market not in SUPPORTED_MARKET_KEYS:
        if _parse_correct_score(selection_key, selection_label):
            market = "correct_score"
        else:
            return "pending"

    if not match_finished and not is_leg_decided(
        market_key=market,
        market_label=market_label,
        selection_key=selection_key,
        selection_label=selection_label,
        home_score=home_score,
        away_score=away_score,
        match_finished=False,
    ):
        return "pending"

    total_goals = home_score + away_score
    home_win = home_score > away_score
    away_win = away_score > home_score
    is_draw = home_score == away_score

    if market == "match_result":
        if selection == "home":
            return "won" if home_win else "lost"
        if selection == "draw":
            return "won" if is_draw else "lost"
        if selection == "away":
            return "won" if away_win else "lost"
        return "pending"

    if market == "both_teams_to_score":
        both_scored = home_score > 0 and away_score > 0
        if selection == "yes":
            return "won" if both_scored else "lost" if match_finished else "pending"
        if selection == "no":
            return "lost" if both_scored else "won" if match_finished else "pending"
        return "pending"

    if market == "double_chance":
        if selection == "home_or_draw":
            return "won" if (home_win or is_draw) else "lost"
        if selection == "home_or_away":
            return "won" if (home_win or away_win) else "lost"
        if selection == "draw_or_away":
            return "won" if (is_draw or away_win) else "lost"
        return "pending"

    line = _over_under_line(market)
    if line is not None:
        if selection.startswith("over_"):
            return "won" if total_goals > line else "lost" if match_finished else "pending"
        if selection.startswith("under_"):
            return "won" if total_goals < line + 0.5 else "lost"
        return "pending"

    if market == "draw_no_bet":
        if is_draw:
            return "void"
        if selection == "home":
            return "won" if home_win else "lost"
        if selection == "away":
            return "won" if away_win else "lost"
        return "pending"

    if market == "correct_score":
        predicted = _parse_correct_score(selection_key, selection_label)
        if predicted is None:
            return "pending"
        return "won" if predicted == (home_score, away_score) else "lost"

    return "pending"


def selection_wins(
    *,
    market_key: str,
    selection_key: str,
    selection_label: str,
    home_score: int,
    away_score: int,
    market_label: str = "",
) -> bool:
    outcome = resolve_leg_outcome(
        market_key=market_key,
        market_label=market_label,
        selection_key=selection_key,
        selection_label=selection_label,
        home_score=home_score,
        away_score=away_score,
        match_finished=True,
    )
    return outcome == "won"
