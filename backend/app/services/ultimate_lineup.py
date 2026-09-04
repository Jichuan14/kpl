"""Team-neutral, artifact-backed lineup search for peak-duel games.

The optimizer reads existing season artifacts and computes results in memory.
It deliberately excludes team strength, team familiarity, team pair statistics,
and draft-policy preferences. No trained or generated artifact is written.
"""

from __future__ import annotations

import json
import math
import statistics
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Iterable

from app.services.analysis_pipeline import OUTPUT_ROOT
from app.services.draft_simulator import load_model
from app.services.lineup_value import (
    ALLY_RULES,
    COUNTER_RULES,
    TACTICAL_ROLES_PATH,
    LineupValueModel,
    load_lineup_value_model,
)


ROLE_IDS = (6, 2, 5, 7, 4)
ROLE_NAMES = {2: "mid", 4: "roam", 5: "jungle", 6: "clash", 7: "farm"}
ROLE_CANDIDATE_LIMIT = 16
BEAM_WIDTH = 1500
META_SCENARIO_LIMIT = 50
_CACHE: dict[str, tuple[tuple[int, ...], dict[str, Any]]] = {}
_OPTIMIZER_CACHE: dict[str, tuple[tuple[int, ...], "UltimateLineupOptimizer"]] = {}
_COUNTER_CACHE: dict[tuple[str, tuple[int, ...]], tuple[tuple[int, ...], dict[str, Any]]] = {}


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(f"Required lineup input is missing: {path}")
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _artifact_signature(league_id: str) -> tuple[int, ...]:
    root = OUTPUT_ROOT / league_id
    paths = (
        root / "draft_model.json",
        root / "lineup_value_model.json",
        root / "meta_hero_stats.jsonl",
        root / "pick_synergy_stats.jsonl",
        root / "counter_pick_stats.jsonl",
        TACTICAL_ROLES_PATH,
    )
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Ultimate-lineup inputs are incomplete: " + ", ".join(path.name for path in missing)
        )
    return tuple(path.stat().st_mtime_ns for path in paths)


def _relationship_score(row: dict[str, Any]) -> float:
    lift = max(1.0, float(row.get("smoothed_lift") or 1.0))
    selections = max(0.0, float(row.get("selection_count") or 0.0))
    support = selections / (selections + 8.0)
    return _clamp(math.log2(lift) / 3.0) * support


def _best_relationships(
    rows: Iterable[dict[str, Any]],
    *,
    source_key: str,
    target_key: str,
    symmetric: bool,
) -> dict[tuple[int, int], float]:
    result: dict[tuple[int, int], float] = {}
    for row in rows:
        if (
            row.get("context_level") != "overall"
            or row.get("is_peak_battle")
            or int(row.get("selection_count") or 0) < 2
        ):
            continue
        source = int(row[source_key])
        target = int(row[target_key])
        key = tuple(sorted((source, target))) if symmetric else (source, target)
        result[key] = max(result.get(key, 0.0), _relationship_score(row))
    return result


def _rule_density(
    source_id: int,
    target_id: int,
    mechanics: dict[int, dict[str, float]],
    rules: Iterable[tuple[Iterable[str], Iterable[str]]],
) -> float:
    source = mechanics.get(int(source_id), {})
    target = mechanics.get(int(target_id), {})
    materialized = list(rules)
    if not source or not target or not materialized:
        return 0.0
    hits = sum(
        1
        for source_features, target_features in materialized
        if any(source.get(name, 0.0) > 0 for name in source_features)
        and any(target.get(name, 0.0) > 0 for name in target_features)
    )
    return hits / len(materialized)


class UltimateLineupOptimizer:
    def __init__(self, league_id: str):
        self.league_id = league_id
        self.root = OUTPUT_ROOT / league_id
        self.draft_model = load_model(league_id)
        self.value_model: LineupValueModel = load_lineup_value_model(league_id)
        self.hero_names = {
            int(hero_id): str(name)
            for hero_id, name in self.draft_model.get("hero_names", {}).items()
        }
        self.positions = {
            int(hero_id): tuple(int(position) for position in values if int(position) in ROLE_IDS)
            for hero_id, values in self.draft_model.get("hero_positions", {}).items()
        }

        meta_rows = _jsonl(self.root / "meta_hero_stats.jsonl")
        self.meta = {
            int(row["hero_id"]): (
                0.7 * float(row.get("early_priority_rate") or 0.0)
                + 0.3 * float(row.get("early_priority_ci95_low") or 0.0)
            )
            for row in meta_rows
        }
        self.meta_rank = {
            int(row["hero_id"]): int(row.get("priority_rank") or 999)
            for row in meta_rows
        }
        self.pick_synergy = _best_relationships(
            _jsonl(self.root / "pick_synergy_stats.jsonl"),
            source_key="ally_hero_id",
            target_key="candidate_hero_id",
            symmetric=True,
        )
        self.counter_response = _best_relationships(
            _jsonl(self.root / "counter_pick_stats.jsonl"),
            source_key="candidate_hero_id",
            target_key="opponent_hero_id",
            symmetric=False,
        )
        self._matchup_cache: dict[tuple[int, int], float] = {}

    def _eligible(self, role_id: int) -> list[int]:
        return [hero_id for hero_id, positions in self.positions.items() if role_id in positions]

    def _pair_synergy(self, first: int, second: int) -> float:
        behavior = self.pick_synergy.get(tuple(sorted((int(first), int(second)))), 0.0)
        stat = self.value_model.ally_pair.get(tuple(sorted((int(first), int(second)))))
        outcome = stat.effect(self.value_model.config["synergy_prior"]) if stat else 0.0
        mechanics = (
            _rule_density(first, second, self.value_model.raw_mechanics, ALLY_RULES)
            + _rule_density(second, first, self.value_model.raw_mechanics, ALLY_RULES)
        ) / 2.0
        return 0.55 * behavior + 0.25 * _clamp(0.5 + outcome / 0.22) + 0.20 * mechanics

    def _matchup(self, attacker: int, defender: int) -> float:
        key = (int(attacker), int(defender))
        cached = self._matchup_cache.get(key)
        if cached is not None:
            return cached
        stat = self.value_model.counters.get(key)
        historical = stat.effect(self.value_model.config["counter_prior"]) if stat else 0.0
        mechanics = _rule_density(
            attacker, defender, self.value_model.raw_mechanics, COUNTER_RULES
        ) - _rule_density(
            defender, attacker, self.value_model.raw_mechanics, COUNTER_RULES
        )
        response = self.counter_response.get(key, 0.0) - self.counter_response.get(
            (key[1], key[0]), 0.0
        )
        score = historical + 0.20 * mechanics + 0.06 * response
        self._matchup_cache[key] = score
        return score

    def _composition_score(self, lineup: tuple[int, ...]) -> float:
        profile = self.value_model.composition_profile(lineup)
        mechanics = [self.value_model.raw_mechanics.get(hero_id, {}) for hero_id in lineup]
        has_physical = any(row.get("damage__physical", 0.0) > 0 for row in mechanics)
        has_magic = any(row.get("damage__magic", 0.0) > 0 for row in mechanics)
        checks = (
            float(profile["frontline_count"] >= 1),
            float(profile["primary_engage_count"] >= 1),
            _clamp(profile["hard_cc_count"] / 2.0),
            float(has_physical and has_magic),
            float(profile["mage_count"] <= 2),
            float(profile["tactical_coverage"]),
        )
        return statistics.fmean(checks)

    def _base_score(self, lineup: tuple[int, ...], *, complete: bool) -> float:
        meta = statistics.fmean(self.meta.get(hero_id, 0.0) for hero_id in lineup)
        pairs = list(combinations(lineup, 2))
        synergy = statistics.fmean(self._pair_synergy(*pair) for pair in pairs) if pairs else 0.0
        if not complete:
            return 0.65 * meta + 0.35 * synergy
        composition = self._composition_score(lineup)
        return 0.52 * meta + 0.30 * synergy + 0.18 * composition

    def _lineup_matchup(self, lineup: tuple[int, ...], opponent: tuple[int, ...]) -> float:
        return statistics.fmean(
            self._matchup(attacker, defender)
            for attacker in lineup
            for defender in opponent
        )

    def _roles_are_feasible(self, hero_ids: tuple[int, ...]) -> bool:
        assignments: dict[int, int] = {}

        def assign(hero_id: int, visited: set[int]) -> bool:
            for role_id in self.positions.get(hero_id, ()):
                if role_id in visited:
                    continue
                visited.add(role_id)
                assigned_hero = assignments.get(role_id)
                if assigned_hero is None or assign(assigned_hero, visited):
                    assignments[role_id] = hero_id
                    return True
            return False

        return all(assign(hero_id, set()) for hero_id in hero_ids)

    def _counter_objective(
        self, lineup: tuple[int, ...], target: tuple[int, ...]
    ) -> float:
        base = self._base_score(lineup, complete=len(lineup) == 5)
        counter = statistics.fmean(
            self._matchup(hero_id, target_id)
            for hero_id in lineup
            for target_id in target
        )
        normalized_counter = _clamp(0.5 + counter / 0.35)
        return 0.35 * base + 0.65 * normalized_counter

    def _counter_search(
        self, target: tuple[int, ...]
    ) -> tuple[tuple[int, ...], float, int]:
        meta_candidates = self._meta_candidates()
        counter_candidates: dict[int, list[int]] = {}
        for role_id in ROLE_IDS:
            eligible = self._eligible(role_id)
            counter_choices = sorted(
                eligible,
                key=lambda hero_id: statistics.fmean(
                    self._matchup(hero_id, target_id) for target_id in target
                ),
                reverse=True,
            )[:12]
            counter_candidates[role_id] = list(
                dict.fromkeys([*meta_candidates[role_id][:12], *counter_choices])
            )

        objective = lambda lineup: self._counter_objective(lineup, target)
        beam = self._beam_search(counter_candidates, objective)
        counter = max(beam, key=objective)
        return counter, _clamp(objective(counter)), len(beam)

    def counter_lineup(self, target_hero_ids: Iterable[int]) -> dict[str, Any]:
        """Build the strongest legal, team-neutral response to a completed lineup."""
        target = tuple(int(hero_id) for hero_id in target_hero_ids)
        if len(target) != 5 or len(set(target)) != 5:
            raise ValueError("Counter search requires five distinct target heroes")
        unknown = [hero_id for hero_id in target if hero_id not in self.positions]
        if unknown:
            raise ValueError(f"Unknown target hero ids: {', '.join(map(str, unknown))}")
        if not self._roles_are_feasible(target):
            raise ValueError("Target lineup cannot assign one hero to each lane")

        counter, counter_score, evaluated = self._counter_search(target)
        metrics = {
            "base_score": self._base_score(counter, complete=True),
            "counter_score": counter_score,
        }
        return {
            "league_id": self.league_id,
            "target_hero_ids": list(target),
            "profile": self._profile(
                "generated_counter",
                counter,
                metrics,
                "counter_score",
                counter_target=target,
            ),
            "methodology": {
                "team_neutral": True,
                "creates_artifact": False,
                "allows_mirror_heroes": True,
                "candidate_lineups_evaluated": evaluated,
                "role_ids": list(ROLE_IDS),
            },
        }

    def _beam_search(
        self,
        candidates_by_role: dict[int, list[int]],
        partial_score: Callable[[tuple[int, ...]], float],
    ) -> list[tuple[int, ...]]:
        beam: list[tuple[int, ...]] = [()]
        for role_id in ROLE_IDS:
            expanded: dict[tuple[int, ...], tuple[float, tuple[int, ...]]] = {}
            for lineup in beam:
                used = set(lineup)
                for hero_id in candidates_by_role[role_id]:
                    if hero_id in used:
                        continue
                    candidate = (*lineup, hero_id)
                    identity = tuple(sorted(candidate))
                    score = partial_score(candidate)
                    previous = expanded.get(identity)
                    if previous is None or score > previous[0]:
                        expanded[identity] = (score, candidate)
            beam = [
                lineup
                for _, lineup in sorted(
                    expanded.values(), key=lambda item: item[0], reverse=True
                )[:BEAM_WIDTH]
            ]
            if not beam:
                raise ValueError(f"No legal lineup can fill {ROLE_NAMES[role_id]}")
        return beam

    def _meta_candidates(self) -> dict[int, list[int]]:
        return {
            role_id: sorted(
                self._eligible(role_id),
                key=lambda hero_id: (
                    self.meta.get(hero_id, 0.0),
                    -self.meta_rank.get(hero_id, 999),
                ),
                reverse=True,
            )[:ROLE_CANDIDATE_LIMIT]
            for role_id in ROLE_IDS
        }

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        ordered = sorted(values)
        if not ordered:
            return 0.0
        position = (len(ordered) - 1) * percentile
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        fraction = position - lower
        return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction

    def _scenario_metrics(
        self,
        lineup: tuple[int, ...],
        scenarios: list[tuple[tuple[int, ...], float]],
    ) -> dict[str, float]:
        base = self._base_score(lineup, complete=True)
        utilities = [
            _clamp(base + 0.45 * self._lineup_matchup(lineup, opponent))
            for opponent, _ in scenarios
        ]
        weights = [weight for _, weight in scenarios]
        average = sum(value * weight for value, weight in zip(utilities, weights, strict=True))
        variance = sum(
            weight * (value - average) ** 2
            for value, weight in zip(utilities, weights, strict=True)
        )
        deviation = math.sqrt(max(0.0, variance))
        p10 = self._percentile(utilities, 0.10)
        p90 = self._percentile(utilities, 0.90)
        return {
            "base_score": base,
            "average_score": average,
            "safety_score": _clamp(p10 - 0.25 * deviation),
            "upside_score": _clamp(p90 + 0.10 * deviation),
            "p10_score": p10,
            "p90_score": p90,
            "volatility": deviation,
        }

    def _profile(
        self,
        key: str,
        lineup: tuple[int, ...],
        metrics: dict[str, float],
        objective_key: str,
        *,
        counter_target: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        profile = self.value_model.composition_profile(lineup)
        heroes = [
            {
                "hero_id": hero_id,
                "hero_name": self.hero_names.get(hero_id, str(hero_id)),
                "assigned_position": role_id,
                "assigned_lane": ROLE_NAMES[role_id],
                "eligible_positions": list(self.positions.get(hero_id, ())),
                "meta_priority": self.meta.get(hero_id, 0.0),
            }
            for role_id, hero_id in zip(ROLE_IDS, lineup, strict=True)
        ]
        strongest_pairs = sorted(
            (
                {
                    "hero_ids": list(pair),
                    "hero_names": [self.hero_names.get(hero_id, str(hero_id)) for hero_id in pair],
                    "score": self._pair_synergy(*pair),
                }
                for pair in combinations(lineup, 2)
            ),
            key=lambda row: row["score"],
            reverse=True,
        )[:3]
        result = {
            "key": key,
            "score": metrics[objective_key],
            "hero_ids": list(lineup),
            "heroes": heroes,
            "metrics": metrics,
            "composition": profile,
            "strongest_synergies": strongest_pairs,
        }
        if counter_target is not None:
            result["counter_target_hero_ids"] = list(counter_target)
            result["counter_advantage"] = self._lineup_matchup(lineup, counter_target)
        return result

    def optimize(self) -> dict[str, Any]:
        meta_candidates = self._meta_candidates()
        candidates = self._beam_search(
            meta_candidates,
            lambda lineup: self._base_score(lineup, complete=False),
        )
        ranked_by_base = sorted(
            ((self._base_score(lineup, complete=True), lineup) for lineup in candidates),
            reverse=True,
        )
        scenario_rows = ranked_by_base[:META_SCENARIO_LIMIT]
        maximum = scenario_rows[0][0]
        raw_weights = [math.exp(6.0 * (score - maximum)) for score, _ in scenario_rows]
        weight_total = sum(raw_weights)
        scenarios = [
            (lineup, weight / weight_total)
            for (_, lineup), weight in zip(scenario_rows, raw_weights, strict=True)
        ]

        evaluated = [(self._scenario_metrics(lineup, scenarios), lineup) for lineup in candidates]
        overall_metrics, overall = max(evaluated, key=lambda item: item[0]["average_score"])
        safe_metrics, safe = max(evaluated, key=lambda item: item[0]["safety_score"])
        upside_metrics, upside = max(evaluated, key=lambda item: item[0]["upside_score"])

        counter, counter_score, counter_evaluated = self._counter_search(overall)
        counter_metrics = self._scenario_metrics(counter, scenarios)
        counter_metrics["counter_score"] = counter_score

        return {
            "league_id": self.league_id,
            "profiles": [
                self._profile("best_overall", overall, overall_metrics, "average_score"),
                self._profile("safest", safe, safe_metrics, "safety_score"),
                self._profile("highest_upside", upside, upside_metrics, "upside_score"),
                self._profile(
                    "main_counter",
                    counter,
                    counter_metrics,
                    "counter_score",
                    counter_target=overall,
                ),
            ],
            "methodology": {
                "team_neutral": True,
                "creates_artifact": False,
                "allows_mirror_heroes": True,
                "candidate_lineups_evaluated": len(candidates) + counter_evaluated,
                "meta_scenarios": len(scenarios),
                "role_ids": list(ROLE_IDS),
                "inputs": [
                    "hero positions",
                    "season meta priority",
                    "league pick synergy",
                    "historical counter outcomes",
                    "mechanical relationships",
                    "tactical composition roles",
                ],
                "excluded_inputs": [
                    "team strength",
                    "team hero familiarity",
                    "team pair synergy",
                    "team draft preference",
                ],
            },
        }


def optimize_ultimate_lineups(league_id: str) -> dict[str, Any]:
    """Return cached team-neutral peak-duel profiles without writing files."""
    signature = _artifact_signature(league_id)
    cached = _CACHE.get(league_id)
    if cached and cached[0] == signature:
        return cached[1]
    optimizer = _optimizer_for(league_id, signature)
    result = optimizer.optimize()
    _CACHE[league_id] = (signature, result)
    return result


def optimize_counter_lineup(league_id: str, target_hero_ids: Iterable[int]) -> dict[str, Any]:
    """Return a cached counter to a user-supplied lineup without writing files."""
    signature = _artifact_signature(league_id)
    target = tuple(int(hero_id) for hero_id in target_hero_ids)
    cache_key = (league_id, target)
    cached = _COUNTER_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return cached[1]
    result = _optimizer_for(league_id, signature).counter_lineup(target)
    _COUNTER_CACHE[cache_key] = (signature, result)
    return result


def _optimizer_for(
    league_id: str, signature: tuple[int, ...]
) -> UltimateLineupOptimizer:
    cached = _OPTIMIZER_CACHE.get(league_id)
    if cached and cached[0] == signature:
        return cached[1]
    optimizer = UltimateLineupOptimizer(league_id)
    _OPTIMIZER_CACHE[league_id] = (signature, optimizer)
    return optimizer
