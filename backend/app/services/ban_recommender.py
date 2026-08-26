"""Dedicated opponent-denial recommendations for live ban turns."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_ROOT = REPO_ROOT / "analysis" / "outputs"
SUPPORTED_VERSION = "ban-value-model-v1"
CANDIDATE_POOL_SIZE = 30
RISK_PENALTIES = {"safe": 0.75, "balanced": 0.35, "upside": 0.0}


@dataclass(frozen=True)
class Stat:
    wins: float = 0.0
    games: float = 0.0

    def signal(self, baseline: float, prior: float) -> tuple[float, float]:
        if self.games <= 0:
            return 0.0, 0.0
        rate = (self.wins + prior * baseline) / (self.games + prior)
        return rate - baseline, self.games / (self.games + prior)


def _sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


class BanValueModel:
    def __init__(self, payload: dict[str, Any]):
        if payload.get("version") != SUPPORTED_VERSION:
            raise ValueError(
                f"Unsupported ban value model: {payload.get('version')}"
            )
        self.payload = payload
        self.config = payload.get("config") or {}
        global_row = payload.get("global") or [0.0, 0.0]
        self.global_stat = Stat(float(global_row[0]), float(global_row[1]))
        self.team = self._stats(payload.get("team", []), (str,))
        self.global_pick = self._stats(payload.get("global_pick", []), (int,))
        self.team_pick = self._stats(payload.get("team_pick", []), (str, int))
        self.global_ban = self._stats(payload.get("global_ban", []), (int,))
        self.opponent_ban = self._stats(
            payload.get("opponent_ban", []), (str, int)
        )
        self.ally_pair = self._stats(payload.get("ally_pair", []), (int, int))
        self.counter_pair = self._stats(
            payload.get("counter_pair", []), (int, int)
        )
        self.behavior = {
            (str(row[0]), int(row[1])): float(row[2])
            for row in payload.get("behavior", [])
        }
        self.behavior_total = {
            str(key): float(value)
            for key, value in payload.get("behavior_total", {}).items()
        }
        self.opponent_behavior = {
            (str(row[0]), str(row[1]), int(row[2])): float(row[3])
            for row in payload.get("opponent_behavior", [])
        }
        self.opponent_behavior_total = {
            (str(row[0]), str(row[1])): float(row[2])
            for row in payload.get("opponent_behavior_total", [])
        }
        self.hero_names = {
            int(hero): str(name)
            for hero, name in payload.get("hero_names", {}).items()
        }

    @staticmethod
    def _stats(
        rows: Sequence[Sequence[Any]], converters: Sequence[type]
    ) -> dict[tuple[Any, ...], Stat]:
        result: dict[tuple[Any, ...], Stat] = {}
        width = len(converters)
        for row in rows:
            key = tuple(
                converter(row[index])
                for index, converter in enumerate(converters)
            )
            result[key] = Stat(float(row[width]), float(row[width + 1]))
        return result

    @staticmethod
    def _get(mapping: dict[tuple[Any, ...], Stat], *key: Any) -> Stat:
        return mapping.get(tuple(key), Stat())

    def score(
        self,
        *,
        state: dict[str, Any],
        next_step: dict[str, Any],
        hero_id: int,
        policy_probability: float,
        maximum_policy_probability: float,
    ) -> dict[str, Any]:
        side = str(next_step["side"])
        opponent_side = "red" if side == "blue" else "blue"
        acting_team = str(state[f"{side}_team_id"])
        opponent_team = str(state[f"{opponent_side}_team_id"])
        global_rate = (
            self.global_stat.wins / self.global_stat.games
            if self.global_stat.games
            else 0.5
        )
        acting_stat = self._get(self.team, acting_team)
        opponent_stat = self._get(self.team, opponent_team)
        acting_rate = (
            acting_stat.wins / acting_stat.games
            if acting_stat.games
            else global_rate
        )
        opponent_rate = (
            opponent_stat.wins / opponent_stat.games
            if opponent_stat.games
            else global_rate
        )
        global_pick, global_pick_evidence = self._get(
            self.global_pick, hero_id
        ).signal(global_rate, 40.0)
        opponent_pick_stat = self._get(
            self.team_pick, opponent_team, hero_id
        )
        opponent_pick, opponent_pick_evidence = opponent_pick_stat.signal(
            opponent_rate, 18.0
        )
        self_pick_stat = self._get(self.team_pick, acting_team, hero_id)
        self_pick, self_pick_evidence = self_pick_stat.signal(
            acting_rate, 18.0
        )
        global_ban, global_ban_evidence = self._get(
            self.global_ban, hero_id
        ).signal(global_rate, 40.0)
        opponent_ban, opponent_ban_evidence = self._get(
            self.opponent_ban, opponent_team, hero_id
        ).signal(acting_rate, 18.0)
        opponent_preference = opponent_pick_stat.games / max(
            opponent_stat.games, 1.0
        )
        self_preference = self_pick_stat.games / max(acting_stat.games, 1.0)
        opponent_can_pick = hero_id not in {
            int(value)
            for value in state.get(
                f"{opponent_side}_used_previous_battles", []
            )
        }
        acting_team_can_pick = hero_id not in {
            int(value)
            for value in state.get(f"{side}_used_previous_battles", [])
        }
        if not opponent_can_pick:
            global_pick = 0.0
            opponent_pick = 0.0
            opponent_preference = 0.0
        if not acting_team_can_pick:
            self_pick = 0.0
            self_preference = 0.0

        synergy_signals: list[float] = []
        synergy_evidence: list[float] = []
        for visible in state.get(f"{opponent_side}_picks", []):
            pair = tuple(sorted((hero_id, int(visible))))
            signal, evidence = self._get(self.ally_pair, *pair).signal(
                opponent_rate, 24.0
            )
            synergy_signals.append(signal)
            synergy_evidence.append(evidence)
        counter_signals: list[float] = []
        counter_evidence: list[float] = []
        for visible in state.get(f"{side}_picks", []):
            signal, evidence = self._get(
                self.counter_pair, hero_id, int(visible)
            ).signal(opponent_rate, 30.0)
            counter_signals.append(signal)
            counter_evidence.append(evidence)
        context_synergy = (
            sum(synergy_signals) / len(synergy_signals)
            if synergy_signals
            else 0.0
        )
        protects_picks = (
            sum(counter_signals) / len(counter_signals)
            if counter_signals
            else 0.0
        )
        if not opponent_can_pick:
            context_synergy = 0.0
            protects_picks = 0.0
        context = f"{side}|{int(next_step['team_action_type_number'])}"
        global_behavior = self.behavior.get((context, hero_id), 0.0) / max(
            self.behavior_total.get(context, 0.0), 1.0
        )
        opponent_behavior = self.opponent_behavior.get(
            (context, opponent_team, hero_id), 0.0
        ) / max(
            self.opponent_behavior_total.get((context, opponent_team), 0.0),
            1.0,
        )
        learned_behavior = 0.65 * global_behavior + 0.35 * opponent_behavior
        combined_behavior = 0.70 * policy_probability + 0.30 * learned_behavior
        behavior_penalty = 0.08 * math.log(
            max(combined_behavior, 1e-9)
            / max(maximum_policy_probability, 1e-9)
        )

        opponent_denial = (
            0.75 * global_pick
            + 1.35 * opponent_pick
            + 0.35 * opponent_preference
        )
        context_denial = 0.70 * context_synergy + 0.85 * protects_picks
        observed_ban_outcome = 0.45 * global_ban + 0.70 * opponent_ban
        self_opportunity_cost = 0.75 * self_pick + 0.25 * self_preference
        raw_value = (
            opponent_denial
            + context_denial
            + observed_ban_outcome
            - self_opportunity_cost
            + behavior_penalty
        )
        value = _sigmoid(4.0 * raw_value)
        evidence_values = [
            global_pick_evidence,
            opponent_pick_evidence,
            self_pick_evidence,
            global_ban_evidence,
            opponent_ban_evidence,
            *synergy_evidence,
            *counter_evidence,
        ]
        evidence = sum(evidence_values) / len(evidence_values)
        uncertainty = float(self.config.get("uncertainty_scale", 0.08)) * (
            1.0 - evidence
        )
        return {
            "ban_value": value,
            "uncertainty": uncertainty,
            "evidence": evidence,
            "learned_behavior_probability": learned_behavior,
            "components": {
                "opponent_denial": opponent_denial,
                "context_denial": context_denial,
                "observed_ban_outcome": observed_ban_outcome,
                "self_opportunity_cost": self_opportunity_cost,
                "behavior_realism": behavior_penalty,
            },
            "signals": {
                "opponent_hero_preference": opponent_preference,
                "acting_team_hero_preference": self_preference,
                "opponent_hero_effect": opponent_pick,
                "protects_current_picks": protects_picks,
                "opponent_lineup_synergy": context_synergy,
                "opponent_can_pick": opponent_can_pick,
                "acting_team_can_pick": acting_team_can_pick,
            },
        }


_MODEL_CACHE: tuple[Path, int, BanValueModel] | None = None


def ban_value_model_path(league_id: str) -> Path:
    if not league_id or not all(
        character.isalnum() or character in "-_" for character in league_id
    ):
        raise ValueError("Invalid league_id")
    return MODEL_ROOT / league_id / "ban_value_model.json"


def load_ban_value_model(league_id: str) -> BanValueModel:
    global _MODEL_CACHE
    path = ban_value_model_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"Ban value model is missing: {path}")
    modified = path.stat().st_mtime_ns
    if _MODEL_CACHE and _MODEL_CACHE[:2] == (path, modified):
        return _MODEL_CACHE[2]
    payload = json.loads(path.read_text(encoding="utf-8"))
    model = BanValueModel(payload)
    _MODEL_CACHE = (path, modified, model)
    return model


def _explanations(score: dict[str, Any], policy_probability: float) -> list[dict[str, Any]]:
    signals = score["signals"]
    components = score["components"]
    explanations: list[dict[str, Any]] = [
        {
            "code": "behavior_support",
            "value": policy_probability,
            "text": "Supported by the team-aware BP policy.",
        }
    ]
    if signals["opponent_hero_preference"] >= 0.08:
        explanations.append(
            {
                "code": "denies_opponent_comfort",
                "value": signals["opponent_hero_preference"],
                "text": "Removes a frequent opponent-team hero.",
            }
        )
    if signals["protects_current_picks"] > 0:
        explanations.append(
            {
                "code": "protects_current_picks",
                "value": signals["protects_current_picks"],
                "text": "Historically protects the current picks from a counter.",
            }
        )
    if signals["opponent_lineup_synergy"] > 0:
        explanations.append(
            {
                "code": "breaks_opponent_synergy",
                "value": signals["opponent_lineup_synergy"],
                "text": "Removes synergy with the opponent's visible picks.",
            }
        )
    if components["self_opportunity_cost"] > 0.03:
        explanations.append(
            {
                "code": "self_opportunity_cost",
                "value": components["self_opportunity_cost"],
                "text": "Also removes a useful option from the acting team.",
            }
        )
    strongest = max(
        ("opponent_denial", "context_denial", "observed_ban_outcome"),
        key=lambda key: abs(float(components[key])),
    )
    explanations.append(
        {
            "code": "largest_ban_component",
            "component": strongest,
            "value": components[strongest],
            "text": "Largest ban-model component.",
        }
    )
    return explanations


def _behavior_fallback(
    policy: dict[str, Any], *, top_k: int, risk_mode: str
) -> dict[str, Any]:
    rows = []
    for rank, candidate in enumerate(
        policy["next_action_probabilities"][:top_k], 1
    ):
        rows.append(
            {
                **candidate,
                "action": "ban",
                "side": policy["next_step"]["side"],
                "rank": rank,
                "policy_probability": float(candidate["probability"]),
                "expected_advantage": 0.5,
                "robust_advantage": 0.5,
                "advantage_delta_vs_policy_baseline": 0.0,
                "confidence": "low",
                "explanations": [
                    {
                        "code": "behavior_support",
                        "value": float(candidate["probability"]),
                        "text": "Ban-value artifact unavailable; ranked by BP behavior.",
                    }
                ],
                "likely_opponent_responses": [],
            }
        )
    return {
        "recommendations": rows,
        "risk_mode": risk_mode,
        "policy_baseline_advantage": 0.5,
        "recommender": "ban_behavior_fallback",
        "methodology": {
            "ranking": "BP behavior probability fallback",
            "fallback": True,
        },
    }


def recommend_ban(
    league_id: str,
    state: dict[str, Any],
    *,
    policy: dict[str, Any],
    top_k: int,
    risk_mode: str,
) -> dict[str, Any]:
    if policy.get("next_step", {}).get("action") != "ban":
        raise ValueError("Dedicated ban recommendations require a ban turn")
    try:
        model = load_ban_value_model(league_id)
    except FileNotFoundError:
        result = _behavior_fallback(policy, top_k=top_k, risk_mode=risk_mode)
        result.update(
            league_id=league_id,
            model_type=policy["model_type"],
            model_label=policy["model_label"],
            model_generated_at=policy["model_generated_at"],
            next_step=policy["next_step"],
        )
        return result

    candidates = policy["next_action_probabilities"][:CANDIDATE_POOL_SIZE]
    maximum_probability = max(
        (float(row["probability"]) for row in candidates), default=1.0
    )
    evaluated = []
    for row in candidates:
        hero_id = int(row["hero_id"])
        probability = float(row["probability"])
        score = model.score(
            state=state,
            next_step=policy["next_step"],
            hero_id=hero_id,
            policy_probability=probability,
            maximum_policy_probability=maximum_probability,
        )
        robust = score["ban_value"] - RISK_PENALTIES[risk_mode] * score["uncertainty"]
        evaluated.append(
            {
                "hero_id": hero_id,
                "hero_name": str(row.get("hero_name") or model.hero_names.get(hero_id, hero_id)),
                "action": "ban",
                "side": policy["next_step"]["side"],
                "policy_probability": probability,
                "expected_advantage": score["ban_value"],
                "robust_advantage": robust,
                "ban_value_components": score["components"],
                "ban_value_signals": score["signals"],
                "evidence": score["evidence"],
                "uncertainty": score["uncertainty"],
                "confidence": (
                    "high" if score["evidence"] >= 0.55
                    else "medium" if score["evidence"] >= 0.25
                    else "low"
                ),
                "explanations": _explanations(score, probability),
                "likely_opponent_responses": [],
            }
        )
    probability_total = sum(row["policy_probability"] for row in evaluated) or 1.0
    baseline = sum(
        row["expected_advantage"] * row["policy_probability"]
        for row in evaluated
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
            row["expected_advantage"] - baseline
        )
    side = str(policy["next_step"]["side"])
    return {
        "league_id": league_id,
        "model_type": policy["model_type"],
        "model_label": policy["model_label"],
        "model_generated_at": policy["model_generated_at"],
        "next_step": policy["next_step"],
        "acting_team": {
            "side": side,
            "team_id": str(state[f"{side}_team_id"]),
            "team_name": str(state.get(f"{side}_team_name") or ""),
        },
        "risk_mode": risk_mode,
        "policy_baseline_advantage": baseline,
        "recommendations": evaluated[:top_k],
        "recommender": "ban_value",
        "ban_value_model": {
            "version": model.payload["version"],
            "generated_at": model.payload["generated_at"],
            "source": model.payload.get("source", {}),
            "interpretation": model.payload.get("interpretation", ""),
        },
        "candidate_gate": {
            "method": "top behavior-supported legal bans",
            "legal_candidate_count": int(policy["candidate_count"]),
            "evaluated_candidate_count": len(evaluated),
            "pool_size_limit": CANDIDATE_POOL_SIZE,
        },
        "methodology": {
            "ranking": "opponent-denial value plus BP realism minus uncertainty",
            "fallback": False,
            "pick_model_used": False,
        },
    }
