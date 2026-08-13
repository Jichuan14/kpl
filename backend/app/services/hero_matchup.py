"""Favorite-aware hero recommendations for a partially revealed enemy draft."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from app.services.analysis_pipeline import OUTPUT_ROOT

_COUNTER_CACHE: dict[Path, tuple[int, list[dict[str, Any]]]] = {}
PLAYABLE_LANES = {"clash", "mid", "jungle", "farm", "roam"}


def _counter_rows(league_id: str) -> list[dict[str, Any]]:
    if not league_id or not all(
        character.isalnum() or character in "-_" for character in league_id
    ):
        raise ValueError("Invalid league id")
    path = OUTPUT_ROOT / league_id / "counter_pick_stats.jsonl"
    if not path.is_file():
        raise FileNotFoundError(
            f"No counter-pick statistics have been generated for {league_id}"
        )
    modified = path.stat().st_mtime_ns
    cached = _COUNTER_CACHE.get(path)
    if cached and cached[0] == modified:
        return cached[1]
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    _COUNTER_CACHE[path] = (modified, rows)
    return rows


def rank_matchup_recommendations(
    feature_rows: list[dict[str, Any]],
    counter_rows: list[dict[str, Any]],
    favorite_hero_ids: list[int],
    opponent_hero_ids: list[int],
    preferred_lane: str | None = None,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    """Blend historical responses with similarity to a user's favorite pool."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    by_id = {int(row["hero_id"]): row for row in feature_rows}
    favorite_ids = [int(hero_id) for hero_id in favorite_hero_ids]
    if len(favorite_ids) != len(set(favorite_ids)):
        raise ValueError("Favorite heroes must be unique")
    unknown_favorites = [hero_id for hero_id in favorite_ids if hero_id not in by_id]
    if unknown_favorites:
        raise ValueError(f"Favorite hero is unavailable: {unknown_favorites[0]}")
    favorites = [by_id[hero_id] for hero_id in favorite_ids]
    favorite_id_set = set(favorite_ids)
    opponents = [int(hero_id) for hero_id in opponent_hero_ids]
    if not opponents:
        raise ValueError("Select at least one opponent hero")
    if len(opponents) != len(set(opponents)):
        raise ValueError("Opponent heroes must be unique")
    if favorite_id_set.intersection(opponents):
        raise ValueError("A favorite hero cannot also be an opponent pick")
    unknown = [hero_id for hero_id in opponents if hero_id not in by_id]
    if unknown:
        raise ValueError(f"Opponent hero is unavailable: {unknown[0]}")
    if preferred_lane is not None and preferred_lane not in PLAYABLE_LANES:
        raise ValueError("Unsupported preferred lane")

    favorite_lanes = sorted(
        {
            str(favorite.get("primary_lane") or "unknown")
            for favorite in favorites
            if str(favorite.get("primary_lane") or "unknown") != "unknown"
        }
    )
    lane_constraints = [preferred_lane] if preferred_lane else favorite_lanes
    active_favorites = (
        [
            favorite
            for favorite in favorites
            if str(favorite.get("primary_lane") or "unknown") == preferred_lane
        ]
        if preferred_lane
        else favorites
    )
    active_favorite_ids = {int(favorite["hero_id"]) for favorite in active_favorites}
    eligible = [
        row
        for row in feature_rows
        if (
            not lane_constraints
            or str(row.get("primary_lane") or "unknown") in lane_constraints
            or int(row["hero_id"]) in active_favorite_ids
        )
        and int(row["hero_id"]) not in opponents
    ]
    if not eligible:
        eligible = [
            row for row in feature_rows if int(row["hero_id"]) not in opponents
        ]

    distances = (
        {
            int(row["hero_id"]): min(
                math.hypot(
                    float(row.get("x") or 0) - float(favorite.get("x") or 0),
                    float(row.get("y") or 0) - float(favorite.get("y") or 0),
                )
                for favorite in active_favorites or favorites
            )
            for row in eligible
        }
        if favorites
        else {int(row["hero_id"]): 0.0 for row in eligible}
    )
    ordered_distances = sorted(distances.values())
    distance_scale = ordered_distances[max(0, len(ordered_distances) // 2)] or 1.0
    max_usage = max(
        (float(row.get("weighted_bp_action_count") or 0) for row in eligible),
        default=1.0,
    ) or 1.0

    evidence_index = {
        (int(row["opponent_hero_id"]), int(row["candidate_hero_id"])): row
        for row in counter_rows
        if row.get("context_level") == "overall"
        and not row.get("is_peak_battle")
    }
    recommendations: list[dict[str, Any]] = []
    for hero in eligible:
        hero_id = int(hero["hero_id"])
        opponent_evidence: list[dict[str, Any]] = []
        matchup_scores: list[float] = []
        supported_opponents = 0
        total_selections = 0
        for opponent_id in opponents:
            evidence = evidence_index.get((opponent_id, hero_id))
            if evidence is None:
                matchup_scores.append(0.5)
                continue
            selections = int(evidence.get("selection_count") or 0)
            lift = max(float(evidence.get("smoothed_lift") or 1.0), 0.05)
            wins = float(evidence.get("battle_win_count_when_selected") or 0)
            shrunk_win_rate = (wins + 2.0) / (selections + 4.0)
            lift_score = 0.5 + 0.5 * math.tanh(math.log(lift))
            raw_matchup = 0.72 * lift_score + 0.28 * shrunk_win_rate
            support_weight = min(1.0, selections / 8.0)
            matchup_score = 0.5 + (raw_matchup - 0.5) * support_weight
            matchup_scores.append(matchup_score)
            if selections >= 3:
                supported_opponents += 1
            total_selections += selections
            opponent_evidence.append(
                {
                    "opponent_hero_id": opponent_id,
                    "opponent_hero_name": str(
                        by_id[opponent_id].get("hero_name") or opponent_id
                    ),
                    "selections": selections,
                    "smoothed_lift": round(lift, 3),
                    "battle_win_rate": round(
                        float(evidence.get("battle_win_rate_when_selected") or 0),
                        6,
                    ),
                }
            )

        matchup_score = sum(matchup_scores) / len(matchup_scores)
        style_score = math.exp(-distances[hero_id] / distance_scale) if favorites else 0.5
        usage_score = math.sqrt(
            float(hero.get("weighted_bp_action_count") or 0) / max_usage
        )
        score = 100.0 * (
            0.72 * matchup_score + 0.20 * style_score + 0.08 * usage_score
        )
        recommendations.append(
            {
                "hero_id": hero_id,
                "hero_name": str(hero.get("hero_name") or hero_id),
                "primary_lane": str(hero.get("primary_lane") or "unknown"),
                "is_favorite": hero_id in favorite_id_set,
                "score": round(score, 2),
                "matchup_score": round(matchup_score * 100.0, 2),
                "style_similarity": round(style_score, 6),
                "supported_opponents": supported_opponents,
                "opponent_count": len(opponents),
                "evidence_selections": total_selections,
                "opponent_evidence": opponent_evidence,
            }
        )

    recommendations.sort(
        key=lambda row: (
            -row["score"],
            -row["supported_opponents"],
            -row["evidence_selections"],
            row["hero_name"],
        )
    )
    for rank, row in enumerate(recommendations, 1):
        row["rank"] = rank
    favorite_results = [
        row for row in recommendations if int(row["hero_id"]) in active_favorite_ids
    ]
    visible = recommendations[:limit]
    for favorite_result in favorite_results:
        if favorite_result not in visible:
            visible.append(favorite_result)
    return {
        "favorites": [
            {
                "hero_id": int(favorite["hero_id"]),
                "hero_name": str(
                    favorite.get("hero_name") or favorite["hero_id"]
                ),
                "primary_lane": str(favorite.get("primary_lane") or "unknown"),
                "rank": next(
                    (
                        row["rank"]
                        for row in favorite_results
                        if row["hero_id"] == int(favorite["hero_id"])
                    ),
                    None,
                ),
                "candidate_count": len(recommendations),
            }
            for favorite in active_favorites
        ],
        "opponents": [
            {
                "hero_id": hero_id,
                "hero_name": str(by_id[hero_id].get("hero_name") or hero_id),
            }
            for hero_id in opponents
        ],
        "recommendations": visible,
        "methodology": {
            "matchup_weight": 0.72,
            "favorite_style_weight": 0.20,
            "uses_favorite_pool": bool(favorites),
            "season_usage_weight": 0.08,
            "lane_constraints": lane_constraints,
            "selected_lane": preferred_lane,
            "candidate_count": len(recommendations),
            "minimum_supported_selections": 3,
        },
    }


def recommend_heroes(
    league_id: str,
    feature_space: dict[str, Any],
    favorite_hero_ids: list[int],
    opponent_hero_ids: list[int],
    preferred_lane: str | None = None,
    *,
    limit: int = 6,
) -> dict[str, Any]:
    return rank_matchup_recommendations(
        list(feature_space.get("rows") or []),
        _counter_rows(league_id),
        favorite_hero_ids,
        opponent_hero_ids,
        preferred_lane,
        limit=limit,
    )
