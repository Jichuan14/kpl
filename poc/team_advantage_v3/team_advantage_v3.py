#!/usr/bin/env python3
"""Tune and query a mechanics-aware team advantage score."""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations, product
from pathlib import Path
from typing import Any, Sequence

import numpy as np


V3_DIR = Path(__file__).resolve().parent
REPO_ROOT = V3_DIR.parents[1]
V2_PATH = REPO_ROOT / "poc" / "team_score_v2" / "team_score_v2.py"
MECHANICS_PATH = REPO_ROOT / "analysis" / "hero_draft_feature_vectors.json"
DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"
ARTIFACT_DIR = V3_DIR / "artifacts"
MODEL_PATH = ARTIFACT_DIR / "team_advantage_model_v3.json"
SEARCH_PATH = ARTIFACT_DIR / "parameter_search_v3.json"
VALIDATION_PATH = ARTIFACT_DIR / "validation_v3.json"
VERSION = "team-advantage-poc-v3"


def import_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V2 = import_module("team_score_v2_for_v3", V2_PATH)
V1 = V2.V1

FEATURE_NAMES = (
    "team_strength",
    "hero_familiarity",
    "mechanics_role_coverage",
    "mechanics_ally_compatibility",
    "mechanics_counter_advantage",
    "league_pair_synergy",
    "team_pair_synergy",
    "historical_counter_advantage",
)

LANE_FEATURES = (
    "lane__clash",
    "lane__mid",
    "lane__jungle",
    "lane__farm",
    "lane__roam",
)

# A rule fires when any source mechanic on one hero complements/counters any
# target mechanic on another hero. Coefficients are learned; rules only expose
# interpretable candidate interactions.
ALLY_RULES = (
    (("mechanic__debuff_armor",), ("damage__physical",)),
    (("mechanic__debuff_magic_defense",), ("damage__magic",)),
    (("mechanic__support_ally_heal",), ("condition__channel_or_charge", "condition__low_health_condition")),
    (("mechanic__support_ally_shield", "mechanic__defense_shield"), ("condition__channel_or_charge", "condition__directional")),
    (("mechanic__support_ally_reposition",), ("condition__directional", "condition__distance_scaling")),
    (("control__strong",), ("mechanic__damage_execute", "mechanic__damage_percent_health")),
)

COUNTER_RULES = (
    (("mechanic__control_anti_mobility",), ("mechanic__mobility_dash", "mechanic__mobility_speed_boost", "mechanic__mobility_teleport", "mechanic__mobility_wall_traverse")),
    (("mechanic__defense_cleanse", "mechanic__defense_control_immunity"), ("control__strong",)),
    (("mechanic__defense_projectile_block",), ("mechanic__vulnerability_projectile_blockable",)),
    (("mechanic__debuff_healing_reduction",), ("mechanic__support_ally_heal", "mechanic__sustain_heal", "mechanic__sustain_lifesteal")),
    (("mechanic__debuff_shield_break",), ("mechanic__support_ally_shield", "mechanic__defense_shield")),
    (("mechanic__damage_execute",), ("condition__low_health_condition", "mechanic__sustain_heal")),
)

REQUIRED_FEATURES = sorted(
    set(LANE_FEATURES)
    | {
        feature
        for rules in (ALLY_RULES, COUNTER_RULES)
        for source, target in rules
        for feature in (*source, *target)
    }
)


def load_raw_mechanics(
    path: Path,
) -> tuple[dict[int, dict[str, float]], dict[int, dict[str, list[float]]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = list(payload["feature_names"])
    indexes = {name: index for index, name in enumerate(names)}
    missing = [name for name in REQUIRED_FEATURES if name not in indexes]
    if missing:
        raise ValueError(f"Mechanics artifact is missing required features: {missing}")
    raw: dict[int, dict[str, float]] = {}
    for row in payload["rows"]:
        hero_id = int(row.get("hero_id") or 0)
        vector = row.get("vector") or []
        if hero_id > 0 and len(vector) == len(names):
            raw[hero_id] = {
                name: float(vector[indexes[name]]) for name in REQUIRED_FEATURES
            }

    # Reuse V2's role-coverage implementation through its expected grouped map.
    grouped = {
        hero_id: {
            "roles": [values[name] for name in LANE_FEATURES],
            "damage": [0.0, 0.0, 0.0],
            "control": [0.0],
            "mobility": [0.0],
            "sustain_support": [0.0],
        }
        for hero_id, values in raw.items()
    }
    metadata = {
        "path": str(path.resolve()),
        "schema_version": payload.get("schema_version"),
        "hero_count": len(raw),
        "required_features": REQUIRED_FEATURES,
        "ally_rules": ALLY_RULES,
        "counter_rules": COUNTER_RULES,
    }
    return raw, grouped, metadata


def rule_density(
    sources: Sequence[int],
    targets: Sequence[int],
    mechanics: dict[int, dict[str, float]],
    rules: Sequence[tuple[Sequence[str], Sequence[str]]],
    *,
    exclude_self: bool,
) -> tuple[float, float]:
    hits = 0.0
    opportunities = 0
    known_pairs = 0
    for source in sources:
        for target in targets:
            if exclude_self and source == target:
                continue
            opportunities += len(rules)
            source_values = mechanics.get(source)
            target_values = mechanics.get(target)
            if source_values is None or target_values is None:
                continue
            known_pairs += 1
            for source_features, target_features in rules:
                if any(source_values[name] > 0 for name in source_features) and any(
                    target_values[name] > 0 for name in target_features
                ):
                    hits += 1.0
    density = hits / opportunities if opportunities else 0.0
    pair_count = len(sources) * len(targets) - (
        len(set(sources) & set(targets)) if exclude_self else 0
    )
    coverage = known_pairs / pair_count if pair_count else 0.0
    return density, coverage


def ally_compatibility(
    heroes: Sequence[int], mechanics: dict[int, dict[str, float]]
) -> tuple[float, float]:
    return rule_density(
        heroes, heroes, mechanics, ALLY_RULES, exclude_self=True
    )


def mechanics_counter(
    heroes_a: Sequence[int],
    heroes_b: Sequence[int],
    mechanics: dict[int, dict[str, float]],
) -> tuple[float, float]:
    a_to_b, coverage_a = rule_density(
        heroes_a, heroes_b, mechanics, COUNTER_RULES, exclude_self=False
    )
    b_to_a, coverage_b = rule_density(
        heroes_b, heroes_a, mechanics, COUNTER_RULES, exclude_self=False
    )
    return a_to_b - b_to_a, (coverage_a + coverage_b) / 2.0


@dataclass
class HistoricalStateV3(V2.HistoricalStateV2):
    raw_mechanics: dict[int, dict[str, float]] = field(default_factory=dict)

    def features(
        self,
        team_a_id: str,
        heroes_a: Sequence[int],
        team_b_id: str,
        heroes_b: Sequence[int],
    ) -> tuple[list[float], dict[str, float]]:
        v2_features, evidence = super().features(
            team_a_id, heroes_a, team_b_id, heroes_b
        )
        ally_a, ally_coverage_a = ally_compatibility(heroes_a, self.raw_mechanics)
        ally_b, ally_coverage_b = ally_compatibility(heroes_b, self.raw_mechanics)
        counter, counter_coverage = mechanics_counter(
            heroes_a, heroes_b, self.raw_mechanics
        )
        evidence["mechanics_ally_coverage"] = (
            ally_coverage_a + ally_coverage_b
        ) / 2.0
        evidence["mechanics_counter_coverage"] = counter_coverage
        return (
            [
                v2_features[0],
                v2_features[1],
                v2_features[2],
                ally_a - ally_b,
                counter,
                v2_features[6],
                v2_features[7],
                v2_features[8],
            ],
            evidence,
        )

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        raw_mechanics: dict[int, dict[str, float]],
        grouped_mechanics: dict[int, dict[str, list[float]]],
    ) -> "HistoricalStateV3":
        v2 = V2.HistoricalStateV2.from_dict(payload, grouped_mechanics)
        state = cls(
            elo_k=v2.elo_k,
            season_decay=v2.season_decay,
            familiarity_prior=v2.familiarity_prior,
            synergy_prior=v2.synergy_prior,
            counter_prior=v2.counter_prior,
            team_pair_prior=v2.team_pair_prior,
            mechanics=grouped_mechanics,
            raw_mechanics=raw_mechanics,
        )
        for name in (
            "ratings",
            "team_games",
            "team_wins",
            "team_hero",
            "ally_pair",
            "counters",
            "team_pair",
        ):
            setattr(state, name, getattr(v2, name))
        return state


def make_state(
    config: dict[str, float],
    raw: dict[int, dict[str, float]],
    grouped: dict[int, dict[str, list[float]]],
) -> HistoricalStateV3:
    return HistoricalStateV3(
        elo_k=config["elo_k"],
        season_decay=config["season_decay"],
        familiarity_prior=config["familiarity_prior"],
        synergy_prior=config["synergy_prior"],
        counter_prior=config["counter_prior"],
        team_pair_prior=config["team_pair_prior"],
        mechanics=grouped,
        raw_mechanics=raw,
    )


def fit_advantage_model(
    features: np.ndarray,
    outcomes: np.ndarray,
    *,
    l2: float,
) -> dict[str, Any]:
    """Fit a monotonic advantage model and recalibrate its intercept."""
    model = V1.fit_logistic(features, outcomes, l2=l2)
    coefficients = np.maximum(np.asarray(model["coefficients"], dtype=float), 0.0)
    means = np.asarray(model["means"], dtype=float)
    scales = np.asarray(model["scales"], dtype=float)
    base_logits = ((features - means) / scales) @ coefficients
    intercept = float(model["intercept"])
    for _iteration in range(50):
        probabilities = 1.0 / (
            1.0 + np.exp(-np.clip(intercept + base_logits, -30.0, 30.0))
        )
        gradient = float(np.sum(probabilities - outcomes))
        hessian = max(float(np.sum(probabilities * (1.0 - probabilities))), 1e-9)
        step = gradient / hessian
        intercept -= step
        if abs(step) < 1e-10:
            break
    model["coefficients"] = coefficients.tolist()
    model["intercept"] = intercept
    model["constraint"] = "all advantage coefficients are nonnegative"
    return model


def chronological_predictions(
    features: np.ndarray,
    outcomes: np.ndarray,
    leagues: Sequence[str],
    test_leagues: Sequence[str],
    *,
    l2: float,
    columns: Sequence[int] = tuple(range(len(FEATURE_NAMES))),
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]]]:
    league_array = np.asarray(leagues)
    ordered = list(dict.fromkeys(leagues))
    pooled_y: list[np.ndarray] = []
    pooled_p: list[np.ndarray] = []
    folds: list[dict[str, Any]] = []
    for test_league in test_leagues:
        position = ordered.index(test_league)
        train_mask = np.isin(league_array, ordered[:position])
        test_mask = league_array == test_league
        model = fit_advantage_model(
            features[train_mask][:, columns], outcomes[train_mask], l2=l2
        )
        predictions = V1.predict_probabilities(model, features[test_mask][:, columns])
        pooled_y.append(outcomes[test_mask])
        pooled_p.append(predictions)
        folds.append(
            {
                "test_league_id": test_league,
                "metrics": V1.metrics(outcomes[test_mask], predictions),
            }
        )
    return np.concatenate(pooled_y), np.concatenate(pooled_p), folds


def parameter_candidates(trials: int, seed: int) -> list[dict[str, float]]:
    space = {
        "elo_k": (16.0, 24.0, 32.0, 40.0),
        "season_decay": (0.4, 0.55, 0.7, 0.85),
        "familiarity_prior": (8.0, 16.0, 32.0, 64.0),
        "synergy_prior": (12.0, 24.0, 48.0, 96.0),
        "counter_prior": (20.0, 40.0, 80.0, 140.0),
        "team_pair_prior": (30.0, 60.0, 120.0, 200.0),
        "l2": (1.0, 4.0, 12.0, 32.0),
    }
    keys = tuple(space)
    all_candidates = [dict(zip(keys, values)) for values in product(*(space[key] for key in keys))]
    default = {
        "elo_k": 24.0,
        "season_decay": 0.65,
        "familiarity_prior": 12.0,
        "synergy_prior": 20.0,
        "counter_prior": 30.0,
        "team_pair_prior": 45.0,
        "l2": 2.0,
    }
    generator = random.Random(seed)
    generator.shuffle(all_candidates)
    selected = [default, *all_candidates[: max(0, trials - 1)]]
    return selected[:trials]


def battles_through_league(
    battles: Sequence[Any], target_league_id: str
) -> tuple[list[Any], list[str]]:
    """Exclude seasons after the requested management-pipeline season."""
    chronological_leagues = list(
        dict.fromkeys(battle.league_id for battle in battles)
    )
    if target_league_id not in chronological_leagues:
        raise ValueError(
            f"Target league {target_league_id!r} has no completed battles"
        )
    target_index = chronological_leagues.index(target_league_id)
    included_leagues = chronological_leagues[: target_index + 1]
    included = set(included_leagues)
    return (
        [battle for battle in battles if battle.league_id in included],
        included_leagues,
    )


def tune(args: argparse.Namespace) -> int:
    raw, grouped, mechanics_metadata = load_raw_mechanics(args.mechanics)
    battles, team_names, hero_names = V1.load_battles(args.db)
    if args.target_league_id:
        battles, all_leagues = battles_through_league(
            battles, args.target_league_id
        )
        active_team_ids = {
            team_id
            for battle in battles
            for team_id in (battle.team_a_id, battle.team_b_id)
        }
        active_hero_ids = {
            hero_id
            for battle in battles
            for hero_id in (*battle.heroes_a, *battle.heroes_b)
        }
        team_names = {
            team_id: name
            for team_id, name in team_names.items()
            if team_id in active_team_ids
        }
        hero_names = {
            hero_id: name
            for hero_id, name in hero_names.items()
            if hero_id in active_hero_ids
        }
    else:
        all_leagues = list(
            dict.fromkeys(battle.league_id for battle in battles)
        )
    if len(all_leagues) < 3:
        raise ValueError("At least three chronological seasons are required")
    final_test_league = all_leagues[-1]
    development_leagues = all_leagues[:-1]
    development_test_leagues = development_leagues[1:]
    results: list[dict[str, Any]] = []
    cached_best: tuple[np.ndarray, np.ndarray, list[str], HistoricalStateV3] | None = None
    best_config: dict[str, float] | None = None

    def selection_key(metrics: dict[str, Any]) -> tuple[float, float]:
        if args.objective == "auc":
            return -float(metrics["auc"]), float(metrics["log_loss"])
        return float(metrics["log_loss"]), -float(metrics["auc"])

    for index, config in enumerate(parameter_candidates(args.trials, args.seed), 1):
        state = make_state(config, raw, grouped)
        features, outcomes, leagues, final_state = V1.build_prequential_features(
            battles, state=state
        )
        dev_y, dev_p, _folds = chronological_predictions(
            features,
            outcomes,
            leagues,
            development_test_leagues,
            l2=config["l2"],
        )
        dev_metrics = V1.metrics(dev_y, dev_p)
        result = {"trial": index, "config": config, "development": dev_metrics}
        results.append(result)
        if best_config is None or selection_key(dev_metrics) < selection_key(
            results[0]["development"]
        ):
            # Keep the current best at position zero so the comparison remains simple.
            results[0], results[-1] = results[-1], results[0]
            best_config = config
            cached_best = (features, outcomes, leagues, final_state)
        elif index == 1:
            best_config = config
            cached_best = (features, outcomes, leagues, final_state)

    assert best_config is not None and cached_best is not None
    features, outcomes, leagues, final_state = cached_best
    development_y, development_p, development_folds = chronological_predictions(
        features,
        outcomes,
        leagues,
        development_test_leagues,
        l2=best_config["l2"],
    )
    final_y, final_p, final_folds = chronological_predictions(
        features,
        outcomes,
        leagues,
        [final_test_league],
        l2=best_config["l2"],
    )

    # Compare simpler baselines on the untouched final season using identical history.
    comparisons = {
        "elo_only": (0,),
        "team_and_familiarity": (0, 1),
        "without_mechanics": (0, 1, 5, 6, 7),
        "full_v3": tuple(range(len(FEATURE_NAMES))),
    }
    final_comparison: dict[str, Any] = {}
    for name, columns in comparisons.items():
        y_values, probabilities, _ = chronological_predictions(
            features,
            outcomes,
            leagues,
            [final_test_league],
            l2=best_config["l2"],
            columns=columns,
        )
        final_comparison[name] = V1.metrics(y_values, probabilities)

    final_model = fit_advantage_model(features, outcomes, l2=best_config["l2"])
    final_model["feature_names"] = list(FEATURE_NAMES)
    generated_at = datetime.now(timezone.utc).isoformat()
    search_payload = {
        "version": VERSION,
        "generated_at": generated_at,
        "seed": args.seed,
        "trial_count": len(results),
        "selection_data": development_leagues,
        "untouched_final_test_league": final_test_league,
        "selection_rule": (
            "maximum development AUC; minimum log loss tie-breaker"
            if args.objective == "auc"
            else "minimum development log loss; maximum AUC tie-breaker"
        ),
        "objective": args.objective,
        "best_config": best_config,
        "trials": sorted(
            results,
            key=lambda row: selection_key(row["development"]),
        ),
    }
    validation_payload = {
        "version": VERSION,
        "generated_at": generated_at,
        "feature_names": list(FEATURE_NAMES),
        "best_config": best_config,
        "development": {
            "metrics": V1.metrics(development_y, development_p),
            "folds": development_folds,
        },
        "untouched_final_test": {
            "league_id": final_test_league,
            "metrics": V1.metrics(final_y, final_p),
            "folds": final_folds,
            "comparisons": final_comparison,
        },
        "interpretation": "Advantage-ranking POC; score is not asserted to be a calibrated win probability.",
    }
    model_payload = {
        "version": VERSION,
        "generated_at": generated_at,
        "best_config": best_config,
        "model": final_model,
        "state": final_state.to_dict(),
        "raw_mechanics": {str(key): value for key, value in raw.items()},
        "grouped_mechanics": {str(key): value for key, value in grouped.items()},
        "mechanics_metadata": mechanics_metadata,
        "team_names": team_names,
        "hero_names": {str(key): value for key, value in hero_names.items()},
        "source": {
            "database": str(args.db.resolve()),
            "database_open_mode": "read_only",
            "battle_count": len(battles),
            "league_ids": all_leagues,
            "target_league_id": args.target_league_id,
        },
        "warning": "Optimized advantage score POC; do not display as literal win probability.",
    }
    for path, payload in (
        (args.search_output, search_payload),
        (args.validation_output, validation_payload),
        (args.model_output, model_payload),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(validation_payload["untouched_final_test"], indent=2))
    print(f"Best configuration: {best_config}")
    return 0


def load_model(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != VERSION:
        raise ValueError(f"Unsupported model version: {payload.get('version')}")
    return payload


def score(args: argparse.Namespace) -> int:
    payload = load_model(args.model)
    team_names = {str(key): str(value) for key, value in payload["team_names"].items()}
    hero_names = {int(key): str(value) for key, value in payload["hero_names"].items()}
    raw = {int(key): value for key, value in payload["raw_mechanics"].items()}
    grouped = {int(key): value for key, value in payload["grouped_mechanics"].items()}
    team_a = V1.resolve_team(args.team_a, team_names)
    team_b = V1.resolve_team(args.team_b, team_names)
    if team_a == team_b:
        raise ValueError("The two teams must be different")
    heroes_a = V1.resolve_heroes(args.heroes_a, hero_names)
    heroes_b = V1.resolve_heroes(args.heroes_b, hero_names)
    if set(heroes_a) & set(heroes_b):
        raise ValueError("A hero cannot appear on both sides")
    state = HistoricalStateV3.from_dict(payload["state"], raw, grouped)
    features, evidence = state.features(team_a, heroes_a, team_b, heroes_b)
    feature_array = np.asarray([features], dtype=float)
    model = payload["model"]
    advantage_a = float(V1.predict_probabilities(model, feature_array)[0])
    contributions = (
        (feature_array[0] - np.asarray(model["means"]))
        / np.asarray(model["scales"])
        * np.asarray(model["coefficients"])
    )
    named = {
        name: round(float(value), 4)
        for name, value in zip(FEATURE_NAMES, contributions)
    }
    result = {
        "version": VERSION,
        "interpretation": "relative matchup advantage, not literal win probability",
        "team_a": {
            "team_id": team_a,
            "team_name": team_names[team_a],
            "side": "camp1_blue",
            "score": round(advantage_a * 100.0, 1),
            "heroes": [{"hero_id": hero, "hero_name": hero_names[hero]} for hero in heroes_a],
        },
        "team_b": {
            "team_id": team_b,
            "team_name": team_names[team_b],
            "side": "camp2_red",
            "score": round((1.0 - advantage_a) * 100.0, 1),
            "heroes": [{"hero_id": hero, "hero_name": hero_names[hero]} for hero in heroes_b],
        },
        "grouped_log_odds_contributions_to_team_a": {
            "team_strength": named["team_strength"],
            "selected_hero_familiarity": named["hero_familiarity"],
            "hero_synergy": round(named["mechanics_role_coverage"] + named["mechanics_ally_compatibility"] + named["league_pair_synergy"] + named["team_pair_synergy"], 4),
            "hero_counters": round(named["mechanics_counter_advantage"] + named["historical_counter_advantage"], 4),
        },
        "detailed_log_odds_contributions_to_team_a": named,
        "evidence": {key: round(float(value), 4) for key, value in evidence.items()},
        "warning": payload["warning"],
    }
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"Wrote score: {args.output}")
    else:
        print(serialized, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    tune_parser = subparsers.add_parser("tune")
    tune_parser.add_argument("--db", type=Path, default=DB_PATH)
    tune_parser.add_argument("--mechanics", type=Path, default=MECHANICS_PATH)
    tune_parser.add_argument("--trials", type=int, default=96)
    tune_parser.add_argument("--seed", type=int, default=17)
    tune_parser.add_argument("--objective", choices=("auc", "log_loss"), default="auc")
    tune_parser.add_argument("--target-league-id")
    tune_parser.add_argument("--model-output", type=Path, default=MODEL_PATH)
    tune_parser.add_argument("--search-output", type=Path, default=SEARCH_PATH)
    tune_parser.add_argument("--validation-output", type=Path, default=VALIDATION_PATH)
    tune_parser.set_defaults(handler=tune)

    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--model", type=Path, default=MODEL_PATH)
    score_parser.add_argument("--team-a", required=True)
    score_parser.add_argument("--heroes-a", required=True)
    score_parser.add_argument("--team-b", required=True)
    score_parser.add_argument("--heroes-b", required=True)
    score_parser.add_argument("--output", type=Path)
    score_parser.set_defaults(handler=score)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "tune" and args.trials < 1:
        raise ValueError("--trials must be positive")
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
