"""Behavior-gated, value-ranked recommendations for a live KPL draft state."""

from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Any, Iterable

from app.services.draft_simulator import (
    predict_next_action,
    sample_forced_draft_completions,
)
from app.services.lineup_value import LineupValueModel, load_lineup_value_model
from app.services.ban_recommender import recommend_ban


CANDIDATE_POOL_SIZE = 10
ROLLOUTS_PER_CANDIDATE = 12
RISK_PENALTIES = {"safe": 0.75, "balanced": 0.35, "upside": 0.0}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _mean_mapping(rows: Iterable[dict[str, float]]) -> dict[str, float]:
    materialized = list(rows)
    if not materialized:
        return {}
    keys = set().union(*(row.keys() for row in materialized))
    return {
        key: statistics.fmean(float(row.get(key, 0.0)) for row in materialized)
        for key in sorted(keys)
    }


def _likely_responses(
    completions: list[dict[str, Any]], acting_side: str, limit: int = 3
) -> list[dict[str, Any]]:
    counts: Counter[tuple[int, str, str, str]] = Counter()
    completed_count = 0
    for completion in completions:
        if not completion.get("completed"):
            continue
        completed_count += 1
        for action in completion.get("path", [])[1:]:
            if action.get("side") != acting_side:
                key = (
                    int(action["hero_id"]),
                    str(action["hero_name"]),
                    str(action["action"]),
                    str(action["side"]),
                )
                counts[key] += 1
                break
    return [
        {
            "hero_id": hero_id,
            "hero_name": hero_name,
            "action": action,
            "side": side,
            "probability": count / completed_count if completed_count else 0.0,
        }
        for (hero_id, hero_name, action, side), count in counts.most_common(limit)
    ]


def _candidate_explanations(
    *,
    action: str,
    policy_probability: float,
    familiarity: dict[str, float],
    before: dict[str, float],
    after: dict[str, float],
    value_components: dict[str, float],
) -> list[dict[str, Any]]:
    explanations: list[dict[str, Any]] = [
        {
            "code": "behavior_support",
            "value": policy_probability,
            "text": "Supported by the team-aware draft policy.",
        }
    ]
    if action == "pick":
        for key, code, text in (
            ("frontline_count", "adds_frontline", "Adds a frontline option."),
            ("primary_engage_count", "adds_primary_engage", "Adds primary engage."),
            ("hard_cc_count", "adds_hard_cc", "Adds a reliable hard-control option."),
        ):
            change = float(after.get(key, 0.0)) - float(before.get(key, 0.0))
            if change > 0:
                explanations.append({"code": code, "value": change, "text": text})
        if float(after.get("mage_count", 0.0)) >= 2:
            explanations.append(
                {
                    "code": "mage_redundancy",
                    "value": after["mage_count"],
                    "text": "Creates a multi-mage lineup; historical results flag redundancy risk.",
                }
            )
        if familiarity["evidence"] >= 0.2:
            explanations.append(
                {
                    "code": "team_hero_familiarity",
                    "value": familiarity["effect"],
                    "evidence": familiarity["evidence"],
                    "text": (
                        "The team has positive historical evidence on this hero."
                        if familiarity["effect"] >= 0
                        else "The team's historical results on this hero are below its baseline."
                    ),
                }
            )
    draft_components = {
        key: value
        for key, value in value_components.items()
        if key != "team_strength"
    }
    strongest = max(
        draft_components,
        key=lambda key: abs(draft_components[key]),
        default=None,
    )
    if strongest:
        labels = {
            "team_strength": "Team strength is the largest value-model component.",
            "selected_hero_familiarity": "Selected-hero familiarity is the largest value-model component.",
            "hero_synergy": "Lineup synergy is the largest value-model component.",
            "hero_counters": "Historical counter evidence is the largest value-model component.",
        }
        explanations.append(
            {
                "code": "largest_value_component",
                "component": strongest,
                "value": draft_components[strongest],
                "text": labels[strongest],
            }
        )
    return explanations


def _evaluate_candidate(
    *,
    league_id: str,
    state: dict[str, Any],
    model_type: str,
    policy_row: dict[str, Any],
    seed: int | None,
    risk_mode: str,
    value_model: LineupValueModel,
    next_step: dict[str, Any],
) -> dict[str, Any] | None:
    hero_id = int(policy_row["hero_id"])
    acting_side = str(next_step["side"])
    acting_team_id = str(state[f"{acting_side}_team_id"])
    rollout = sample_forced_draft_completions(
        league_id,
        state,
        forced_first_hero_id=hero_id,
        rollouts=ROLLOUTS_PER_CANDIDATE,
        seed=None if seed is None else seed + hero_id * 1009,
        model_type=model_type,
    )
    completed = [row for row in rollout["completions"] if row.get("completed")]
    scored: list[dict[str, Any]] = []
    for completion in completed:
        terminal = completion["state"]
        blue_picks = [int(value) for value in terminal.get("blue_picks", [])]
        red_picks = [int(value) for value in terminal.get("red_picks", [])]
        if len(blue_picks) != 5 or len(red_picks) != 5:
            continue
        scored.append(
            value_model.score(
                str(state["blue_team_id"]),
                blue_picks,
                str(state["red_team_id"]),
                red_picks,
            )
        )
    if not scored:
        return None

    utilities = [float(row[f"{acting_side}_advantage"]) for row in scored]
    expected = statistics.fmean(utilities)
    uncertainty = statistics.pstdev(utilities) if len(utilities) > 1 else 0.0
    robust = expected - RISK_PENALTIES[risk_mode] * uncertainty
    contribution_multiplier = 1.0 if acting_side == "blue" else -1.0
    value_components = {
        key: value * contribution_multiplier
        for key, value in _mean_mapping(
            row["grouped_contributions"] for row in scored
        ).items()
    }
    evidence = _mean_mapping(row["evidence"] for row in scored)
    final_composition = _mean_mapping(
        row[f"{acting_side}_composition"] for row in scored
    )
    current_picks = [int(value) for value in state.get(f"{acting_side}_picks", [])]
    before = value_model.composition_profile(current_picks)
    after_picks = (
        [*current_picks, hero_id]
        if next_step["action"] == "pick"
        else current_picks
    )
    after = value_model.composition_profile(after_picks)
    familiarity = value_model.team_hero_signal(acting_team_id, hero_id)
    evidence_score = statistics.fmean(
        float(evidence.get(key, 0.0))
        for key in ("hero_familiarity", "team_pair_synergy", "historical_counter")
    )
    completion_rate = len(scored) / ROLLOUTS_PER_CANDIDATE
    confidence = (
        "high"
        if completion_rate == 1.0 and evidence_score >= 0.45 and uncertainty <= 0.06
        else "medium"
        if completion_rate >= 0.8 and evidence_score >= 0.2
        else "low"
    )
    return {
        "hero_id": hero_id,
        "hero_name": str(policy_row.get("hero_name") or value_model.hero_names.get(hero_id, hero_id)),
        "action": str(next_step["action"]),
        "side": acting_side,
        "policy_probability": float(policy_row["probability"]),
        "expected_advantage": expected,
        "robust_advantage": robust,
        "advantage_interval_p10_p90": [
            _percentile(utilities, 0.1),
            _percentile(utilities, 0.9),
        ],
        "rollout_standard_deviation": uncertainty,
        "completed_rollouts": len(scored),
        "rollout_count": ROLLOUTS_PER_CANDIDATE,
        "confidence": confidence,
        "value_components": value_components,
        "evidence": evidence,
        "team_hero_familiarity": familiarity,
        "composition_after_action": after,
        "expected_final_composition": final_composition,
        "likely_opponent_responses": _likely_responses(
            rollout["completions"], acting_side
        ),
        "explanations": _candidate_explanations(
            action=str(next_step["action"]),
            policy_probability=float(policy_row["probability"]),
            familiarity=familiarity,
            before=before,
            after=after,
            value_components=value_components,
        ),
    }


def recommend_lineup(
    league_id: str,
    state: dict[str, Any],
    *,
    model_type: str = "stats",
    seed: int | None = None,
    top_k: int = 3,
    risk_mode: str = "balanced",
) -> dict[str, Any]:
    """Rank realistic next actions by their behavior-guided leaf value."""
    if top_k < 1 or top_k > 5:
        raise ValueError("top_k must be between 1 and 5")
    if risk_mode not in RISK_PENALTIES:
        raise ValueError(f"Unsupported risk mode: {risk_mode}")
    policy = predict_next_action(
        league_id,
        state,
        model_type=model_type,
        limit=200,
    )
    if policy["next_step"]["action"] == "ban":
        return recommend_ban(
            league_id,
            state,
            policy=policy,
            top_k=top_k,
            risk_mode=risk_mode,
        )
    candidates = policy["next_action_probabilities"][:CANDIDATE_POOL_SIZE]
    if not candidates:
        raise ValueError("No behavior-supported candidates are available")
    value_model = load_lineup_value_model(league_id)
    evaluated = [
        result
        for row in candidates
        if (
            result := _evaluate_candidate(
                league_id=league_id,
                state=state,
                model_type=model_type,
                policy_row=row,
                seed=seed,
                risk_mode=risk_mode,
                value_model=value_model,
                next_step=policy["next_step"],
            )
        )
        is not None
    ]
    if not evaluated:
        raise ValueError("Draft rollouts could not produce a complete legal lineup")

    probability_total = sum(row["policy_probability"] for row in evaluated) or 1.0
    policy_baseline = sum(
        row["expected_advantage"] * row["policy_probability"] for row in evaluated
    ) / probability_total
    evaluated.sort(
        key=lambda row: (
            row["robust_advantage"],
            row["expected_advantage"],
            row["policy_probability"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(evaluated, 1):
        row["rank"] = rank
        row["advantage_delta_vs_policy_baseline"] = (
            row["expected_advantage"] - policy_baseline
        )

    next_step = policy["next_step"]
    acting_side = str(next_step["side"])
    return {
        "league_id": league_id,
        "model_type": model_type,
        "model_label": policy["model_label"],
        "model_generated_at": policy["model_generated_at"],
        "next_step": next_step,
        "acting_team": {
            "side": acting_side,
            "team_id": str(state[f"{acting_side}_team_id"]),
            "team_name": str(state.get(f"{acting_side}_team_name") or ""),
        },
        "risk_mode": risk_mode,
        "candidate_gate": {
            "method": "top behavior-supported legal actions",
            "legal_candidate_count": int(policy["candidate_count"]),
            "evaluated_candidate_count": len(evaluated),
            "pool_size_limit": CANDIDATE_POOL_SIZE,
            "minimum_evaluated_policy_probability": min(
                row["policy_probability"] for row in evaluated
            ),
        },
        "policy_baseline_advantage": policy_baseline,
        "recommendations": evaluated[:top_k],
        "value_model": {
            "version": value_model.payload["version"],
            "interpretation": "relative lineup advantage, not literal win probability",
            "warning": value_model.payload.get("warning", ""),
        },
        "global_bp": {
            "blue_previous_hero_count": len(state.get("blue_used_previous_battles", [])),
            "red_previous_hero_count": len(state.get("red_used_previous_battles", [])),
            "legality_enforced_in_every_rollout": True,
        },
        "methodology": {
            "rollouts_per_candidate": ROLLOUTS_PER_CANDIDATE,
            "ranking": "mean terminal advantage minus a risk-mode uncertainty penalty",
            "opponent_model": "behavior-policy sampling",
            "tactical_traits": "explanatory; not hard-coded as automatic bonuses",
            "pick_model_used": True,
        },
        "recommender": "lineup_value",
    }
