"""Coach tools backed by the season lineup-value and recommendation services."""

from typing import Any, Literal

from pydantic import Field

from app.agent.tools.draft import DraftStateArguments
from app.services.lineup_recommender import recommend_lineup
from app.services.lineup_value import load_lineup_value_model

ADVANTAGE_INTERPRETATION = (
    "relative lineup advantage, not literal win probability or an optimal action"
)


class RecommendValueDraftActionArguments(DraftStateArguments):
    """Inputs for value-ranked next pick or ban recommendations."""

    top_k: int = Field(default=3, ge=1, le=5)
    risk_mode: Literal["safe", "balanced", "upside"] = "balanced"
    seed: int | None = None


class ScoreCurrentLineupArguments(DraftStateArguments):
    """Inputs for scoring the completed 5v5 on the active board."""


def _hero_label(hero_id: int, names: dict[int, str]) -> str:
    return str(names.get(int(hero_id)) or hero_id)


def recommend_value_draft_action(
    arguments: RecommendValueDraftActionArguments,
) -> dict[str, Any]:
    """Rank realistic next actions by completed-lineup or ban-denial value."""
    raw = recommend_lineup(
        arguments.league_id,
        arguments.draft_state(),
        model_type=arguments.model_type,
        seed=arguments.seed,
        top_k=int(arguments.top_k),
        risk_mode=arguments.risk_mode,
    )
    recommendations = []
    for row in raw.get("recommendations") or []:
        recommendations.append(
            {
                "rank": row.get("rank"),
                "hero_id": row.get("hero_id"),
                "hero_name": row.get("hero_name"),
                "action": row.get("action"),
                "side": row.get("side"),
                "policy_probability": row.get("policy_probability"),
                "expected_advantage": row.get("expected_advantage"),
                "robust_advantage": row.get("robust_advantage"),
                "advantage_delta_vs_policy_baseline": row.get(
                    "advantage_delta_vs_policy_baseline"
                ),
                "confidence": row.get("confidence"),
                "explanations": [
                    str(item.get("text") or "")
                    for item in (row.get("explanations") or [])
                    if item.get("text")
                ][:4],
                "likely_opponent_responses": (
                    row.get("likely_opponent_responses") or []
                )[:2],
            }
        )
    return {
        "league_id": arguments.league_id,
        "next_step": raw.get("next_step"),
        "acting_team": raw.get("acting_team"),
        "risk_mode": raw.get("risk_mode"),
        "recommendations": recommendations,
        "result_count": len(recommendations),
        "interpretation": ADVANTAGE_INTERPRETATION,
        "warning": (raw.get("value_model") or {}).get("warning")
        or raw.get("warning")
        or "",
    }


def score_current_lineup(arguments: ScoreCurrentLineupArguments) -> dict[str, Any]:
    """Score the completed 5v5 currently on the supplied board."""
    blue = list(arguments.blue_picks)
    red = list(arguments.red_picks)
    if len(blue) != 5 or len(set(blue)) != 5 or len(red) != 5 or len(set(red)) != 5:
        raise ValueError(
            "A completed 5v5 lineup is required. Finish both sides' five picks "
            "before scoring relative lineup advantage."
        )
    model = load_lineup_value_model(arguments.league_id)
    scored = model.score(
        arguments.blue_team_id,
        blue,
        arguments.red_team_id,
        red,
    )
    names = getattr(model, "hero_names", {}) or {}
    blue_composition = scored.get("blue_composition") or {}
    red_composition = scored.get("red_composition") or {}
    return {
        "league_id": arguments.league_id,
        "blue_team": {
            "team_id": arguments.blue_team_id,
            "team_name": arguments.blue_team_name,
            "heroes": [_hero_label(hero_id, names) for hero_id in blue],
        },
        "red_team": {
            "team_id": arguments.red_team_id,
            "team_name": arguments.red_team_name,
            "heroes": [_hero_label(hero_id, names) for hero_id in red],
        },
        "blue_advantage": scored.get("blue_advantage"),
        "red_advantage": scored.get("red_advantage"),
        "grouped_contributions": scored.get("grouped_contributions") or {},
        "blue_composition": {
            "frontline_count": blue_composition.get("frontline_count"),
            "primary_engage_count": blue_composition.get("primary_engage_count"),
            "hard_cc_count": blue_composition.get("hard_cc_count"),
        },
        "red_composition": {
            "frontline_count": red_composition.get("frontline_count"),
            "primary_engage_count": red_composition.get("primary_engage_count"),
            "hard_cc_count": red_composition.get("hard_cc_count"),
        },
        "result_count": 1,
        "interpretation": scored.get("interpretation") or ADVANTAGE_INTERPRETATION,
        "warning": scored.get("warning") or "",
    }
