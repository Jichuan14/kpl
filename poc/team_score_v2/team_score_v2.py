#!/usr/bin/env python3
"""Hierarchical-synergy version of the isolated hero team score POC."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

import numpy as np


V2_DIR = Path(__file__).resolve().parent
REPO_ROOT = V2_DIR.parents[1]
V1_PATH = REPO_ROOT / "poc" / "team_score" / "team_score_poc.py"
MECHANICS_PATH = REPO_ROOT / "analysis" / "hero_draft_feature_vectors.json"
DEFAULT_DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"
DEFAULT_ARTIFACT_DIR = V2_DIR / "artifacts"
DEFAULT_MODEL_PATH = DEFAULT_ARTIFACT_DIR / "team_score_model_v2.json"
DEFAULT_VALIDATION_PATH = DEFAULT_ARTIFACT_DIR / "validation_v2.json"
MODEL_VERSION = "hero-team-score-poc-v2"


def load_v1_module():
    spec = importlib.util.spec_from_file_location("team_score_poc_v1", V1_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load version 1 module: {V1_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V1 = load_v1_module()

FEATURE_NAMES = (
    "team_strength",
    "hero_familiarity",
    "mechanics_role_coverage",
    "mechanics_damage_diversity",
    "mechanics_control_mobility",
    "mechanics_sustain_support",
    "league_pair_synergy",
    "team_pair_synergy",
    "counter_advantage",
)
ACTIVE_FEATURE_INDICES = (0, 1, 2, 6, 8)

MECHANIC_FEATURES = {
    "roles": (
        "lane__clash",
        "lane__mid",
        "lane__jungle",
        "lane__farm",
        "lane__roam",
    ),
    "damage": ("damage__physical", "damage__magic", "damage__true"),
    "control": ("control__strong",),
    "mobility": ("mobility__large",),
    "sustain_support": (
        "heal__reliable_ally_or_team",
        "mechanic__support_ally_heal",
        "mechanic__support_ally_reposition",
        "mechanic__support_ally_shield",
        "mechanic__defense_shield",
        "mechanic__defense_cleanse",
    ),
}


def load_mechanics(path: Path) -> tuple[dict[int, dict[str, list[float]]], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = list(payload["feature_names"])
    indexes = {name: index for index, name in enumerate(names)}
    missing = sorted(
        feature
        for group in MECHANIC_FEATURES.values()
        for feature in group
        if feature not in indexes
    )
    if missing:
        raise ValueError(f"Mechanics artifact is missing features: {missing}")

    heroes: dict[int, dict[str, list[float]]] = {}
    for row in payload["rows"]:
        hero_id = int(row.get("hero_id") or 0)
        vector = row.get("vector") or []
        if hero_id <= 0 or len(vector) != len(names):
            continue
        heroes[hero_id] = {
            group: [float(vector[indexes[name]]) for name in group_names]
            for group, group_names in MECHANIC_FEATURES.items()
        }
    metadata = {
        "path": str(path.resolve()),
        "schema_version": payload.get("schema_version"),
        "hero_count": len(heroes),
        "selected_features": MECHANIC_FEATURES,
    }
    return heroes, metadata


def composition_mechanics(
    hero_ids: Sequence[int], mechanics: dict[int, dict[str, list[float]]]
) -> tuple[list[float], float]:
    known = [mechanics[hero_id] for hero_id in hero_ids if hero_id in mechanics]
    if not known:
        return [0.0, 0.0, 0.0, 0.0], 0.0

    role_coverage = sum(
        any(hero["roles"][index] > 0 for hero in known) for index in range(5)
    ) / 5.0
    damage_diversity = sum(
        any(hero["damage"][index] > 0 for hero in known) for index in range(3)
    ) / 3.0
    strong_control_count = sum(hero["control"][0] > 0 for hero in known)
    large_mobility_count = sum(hero["mobility"][0] > 0 for hero in known)
    control_mobility = (
        min(strong_control_count, 2) / 2.0
        + min(large_mobility_count, 2) / 2.0
    ) / 2.0
    sustain_count = sum(any(value > 0 for value in hero["sustain_support"]) for hero in known)
    sustain_support = min(sustain_count, 2) / 2.0
    return (
        [role_coverage, damage_diversity, control_mobility, sustain_support],
        len(known) / len(hero_ids) if hero_ids else 0.0,
    )


@dataclass
class HistoricalStateV2(V1.HistoricalState):
    team_pair_prior: float = 45.0
    mechanics: dict[int, dict[str, list[float]]] = field(default_factory=dict)
    team_pair: dict[tuple[str, int, int], Any] = field(default_factory=dict)

    def features(
        self,
        team_a_id: str,
        heroes_a: Sequence[int],
        team_b_id: str,
        heroes_b: Sequence[int],
    ) -> tuple[list[float], dict[str, float]]:
        base, evidence = super().features(
            team_a_id, heroes_a, team_b_id, heroes_b
        )
        mechanics_a, coverage_a = composition_mechanics(heroes_a, self.mechanics)
        mechanics_b, coverage_b = composition_mechanics(heroes_b, self.mechanics)
        mechanics_difference = [
            value_a - value_b
            for value_a, value_b in zip(mechanics_a, mechanics_b)
        ]

        team_pair_a = self._average(
            self._stat(
                self.team_pair,
                (team_a_id, *tuple(sorted(pair))),
            ).effect(self.team_pair_prior)
            for pair in combinations(heroes_a, 2)
        )
        team_pair_b = self._average(
            self._stat(
                self.team_pair,
                (team_b_id, *tuple(sorted(pair))),
            ).effect(self.team_pair_prior)
            for pair in combinations(heroes_b, 2)
        )
        team_pair_difference = team_pair_a - team_pair_b
        evidence["mechanics_coverage"] = (coverage_a + coverage_b) / 2.0
        evidence["team_pair_synergy"] = self._average(
            [
                self._stat(
                    self.team_pair, (team_a_id, *tuple(sorted(pair)))
                ).evidence(self.team_pair_prior)
                for pair in combinations(heroes_a, 2)
            ]
            + [
                self._stat(
                    self.team_pair, (team_b_id, *tuple(sorted(pair)))
                ).evidence(self.team_pair_prior)
                for pair in combinations(heroes_b, 2)
            ]
        )
        return (
            [
                base[0],
                base[1],
                *mechanics_difference,
                base[2],
                team_pair_difference,
                base[3],
            ],
            evidence,
        )

    def update(self, battle: Any) -> None:
        expected_a = V1.elo_probability(
            self.rating(battle.team_a_id), self.rating(battle.team_b_id)
        )
        residual_a = float(battle.team_a_won) - expected_a
        residual_b = -residual_a
        super().update(battle)
        for pair in combinations(battle.heroes_a, 2):
            key = (battle.team_a_id, *tuple(sorted(pair)))
            self.team_pair.setdefault(key, V1.ResidualStat()).update(residual_a)
        for pair in combinations(battle.heroes_b, 2):
            key = (battle.team_b_id, *tuple(sorted(pair)))
            self.team_pair.setdefault(key, V1.ResidualStat()).update(residual_b)

    def advance_season(self) -> None:
        super().advance_season()
        for stat in self.team_pair.values():
            stat.decay(self.season_decay)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload["config"]["team_pair_prior"] = self.team_pair_prior
        payload["team_pair"] = [
            [*key, round(stat.residual_sum, 10), round(stat.count, 10)]
            for key, stat in sorted(self.team_pair.items())
        ]
        return payload

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        mechanics: dict[int, dict[str, list[float]]],
    ) -> "HistoricalStateV2":
        config = dict(payload["config"])
        state = cls(mechanics=mechanics, **config)
        base_payload = dict(payload)
        base_payload["config"] = {
            key: value for key, value in config.items() if key != "team_pair_prior"
        }
        base = V1.HistoricalState.from_dict(base_payload)
        state.ratings = base.ratings
        state.team_games = base.team_games
        state.team_wins = base.team_wins
        state.team_hero = base.team_hero
        state.ally_pair = base.ally_pair
        state.counters = base.counters
        for row in payload["team_pair"]:
            state.team_pair[(str(row[0]), int(row[1]), int(row[2]))] = V1.ResidualStat(
                float(row[3]), float(row[4])
            )
        return state


def fit_selected(features: np.ndarray, outcomes: np.ndarray) -> dict[str, Any]:
    model = V1.fit_logistic(features, outcomes, l2=2.0)
    return model


def rolling_validation(
    features: np.ndarray, outcomes: np.ndarray, leagues: Sequence[str]
) -> dict[str, Any]:
    ordered_leagues = list(dict.fromkeys(leagues))
    league_array = np.asarray(leagues)
    specifications = {
        "intercept_only": (),
        "elo_only": (0,),
        "v1_equivalent": (0, 1, 6, 8),
        "selected_v2": ACTIVE_FEATURE_INDICES,
        "v1_plus_team_pair": (0, 1, 6, 7, 8),
        "elo_plus_mechanics": (0, 2, 3, 4, 5),
        "elo_plus_league_pair": (0, 6),
        "elo_plus_team_pair": (0, 7),
        "elo_plus_hierarchical_synergy": (0, 2, 3, 4, 5, 6, 7),
        "elo_plus_counter": (0, 8),
        "all_v2_features": tuple(range(len(FEATURE_NAMES))),
    }
    folds: list[dict[str, Any]] = []
    pooled_outcomes: list[np.ndarray] = []
    pooled_predictions: dict[str, list[np.ndarray]] = defaultdict(list)

    for position, test_league in enumerate(ordered_leagues[1:], 1):
        train_mask = np.isin(league_array, ordered_leagues[:position])
        test_mask = league_array == test_league
        train_x, train_y = features[train_mask], outcomes[train_mask]
        test_x, test_y = features[test_mask], outcomes[test_mask]
        predictions: dict[str, np.ndarray] = {}
        for name, columns in specifications.items():
            if not columns:
                predictions[name] = np.full(len(test_y), float(train_y.mean()))
                continue
            model = fit_selected(train_x[:, columns], train_y)
            predictions[name] = V1.predict_probabilities(model, test_x[:, columns])
        folds.append(
            {
                "test_league_id": test_league,
                "training_league_ids": ordered_leagues[:position],
                "models": {
                    name: V1.metrics(test_y, values)
                    for name, values in predictions.items()
                },
            }
        )
        pooled_outcomes.append(test_y)
        for name, values in predictions.items():
            pooled_predictions[name].append(values)

    all_outcomes = np.concatenate(pooled_outcomes)
    aggregate = {
        name: V1.metrics(all_outcomes, np.concatenate(pooled_predictions[name]))
        for name in specifications
    }
    v2_gain = round(
        aggregate["v1_equivalent"]["log_loss"]
        - aggregate["selected_v2"]["log_loss"],
        6,
    )
    elo_gain = round(
        aggregate["elo_only"]["log_loss"] - aggregate["selected_v2"]["log_loss"],
        6,
    )
    improved_vs_v1 = sum(
        fold["models"]["selected_v2"]["log_loss"]
        < fold["models"]["v1_equivalent"]["log_loss"]
        for fold in folds
    )
    assessment = {
        "verdict": "experimental_v2",
        "log_loss_gain_vs_v1_equivalent": v2_gain,
        "log_loss_gain_vs_elo": elo_gain,
        "held_out_seasons_improved_vs_v1": improved_vs_v1,
        "held_out_season_count": len(folds),
        "material_gain_vs_v1_met": v2_gain >= 0.002,
        "selected_feature_names": [FEATURE_NAMES[index] for index in ACTIVE_FEATURE_INDICES],
        "inactive_experimental_features": [
            FEATURE_NAMES[index]
            for index in range(len(FEATURE_NAMES))
            if index not in ACTIVE_FEATURE_INDICES
        ],
    }
    assessment["release_ready"] = bool(
        assessment["material_gain_vs_v1_met"]
        and improved_vs_v1 >= 4
        and elo_gain >= 0.005
    )
    return {
        "method": "rolling season holdout; all historical features are prequential",
        "folds": folds,
        "aggregate": aggregate,
        "assessment": assessment,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def train(args: argparse.Namespace) -> int:
    mechanics, mechanics_metadata = load_mechanics(args.mechanics)
    battles, team_names, hero_names = V1.load_battles(args.db)
    state = HistoricalStateV2(
        season_decay=args.season_decay,
        team_pair_prior=args.team_pair_prior,
        mechanics=mechanics,
    )
    features, outcomes, leagues, final_state = V1.build_prequential_features(
        battles, state=state
    )
    validation = rolling_validation(features, outcomes, leagues)
    active_features = features[:, ACTIVE_FEATURE_INDICES]
    model = V1.fit_logistic(active_features, outcomes, l2=args.l2)
    model["feature_names"] = [
        FEATURE_NAMES[index] for index in ACTIVE_FEATURE_INDICES
    ]
    model["active_feature_indices"] = list(ACTIVE_FEATURE_INDICES)
    generated_at = datetime.now(timezone.utc).isoformat()
    model_payload = {
        "version": MODEL_VERSION,
        "generated_at": generated_at,
        "source": {
            "database": str(args.db.resolve()),
            "database_open_mode": "read_only",
            "battle_count": len(battles),
            "league_ids": list(dict.fromkeys(leagues)),
            "mechanics": mechanics_metadata,
        },
        "model": model,
        "state": final_state.to_dict(),
        "mechanics": {str(key): value for key, value in mechanics.items()},
        "team_names": team_names,
        "hero_names": {str(key): value for key, value in hero_names.items()},
        "warning": "Experimental version 2; not a production win probability.",
    }
    validation_payload = {
        "version": MODEL_VERSION,
        "generated_at": generated_at,
        "feature_names": list(FEATURE_NAMES),
        **validation,
    }
    write_json(args.model_output, model_payload)
    write_json(args.validation_output, validation_payload)
    print(json.dumps(validation_payload["assessment"], indent=2))
    print(f"Wrote model: {args.model_output}")
    print(f"Wrote validation: {args.validation_output}")
    return 0


def load_model(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != MODEL_VERSION:
        raise ValueError(f"Unsupported model version: {payload.get('version')}")
    return payload


def confidence(evidence: dict[str, float]) -> str:
    history = min(evidence["team_a_games"], evidence["team_b_games"])
    empirical = (
        evidence["hero_familiarity"]
        + evidence["ally_synergy"]
        + evidence["team_pair_synergy"]
        + evidence["counter_advantage"]
    ) / 4.0
    if history >= 60 and empirical >= 0.4 and evidence["mechanics_coverage"] >= 0.9:
        return "high"
    if history >= 20 and empirical >= 0.18 and evidence["mechanics_coverage"] >= 0.7:
        return "medium"
    return "low"


def score_payload(
    payload: dict[str, Any],
    team_a_value: str,
    heroes_a_value: str,
    team_b_value: str,
    heroes_b_value: str,
) -> dict[str, Any]:
    team_names = {str(key): str(value) for key, value in payload["team_names"].items()}
    hero_names = {int(key): str(value) for key, value in payload["hero_names"].items()}
    mechanics = {int(key): value for key, value in payload["mechanics"].items()}
    team_a = V1.resolve_team(team_a_value, team_names)
    team_b = V1.resolve_team(team_b_value, team_names)
    if team_a == team_b:
        raise ValueError("The two teams must be different")
    heroes_a = V1.resolve_heroes(heroes_a_value, hero_names)
    heroes_b = V1.resolve_heroes(heroes_b_value, hero_names)
    if set(heroes_a) & set(heroes_b):
        raise ValueError("A hero cannot appear on both sides")

    state = HistoricalStateV2.from_dict(payload["state"], mechanics)
    raw_features, evidence = state.features(team_a, heroes_a, team_b, heroes_b)
    feature_array = np.asarray([raw_features], dtype=float)
    model = payload["model"]
    active_indices = tuple(int(index) for index in model["active_feature_indices"])
    active_array = feature_array[:, active_indices]
    probability_a = float(V1.predict_probabilities(model, active_array)[0])
    active_contributions = (
        (active_array[0] - np.asarray(model["means"]))
        / np.asarray(model["scales"])
        * np.asarray(model["coefficients"])
    )
    contributions = np.zeros(len(FEATURE_NAMES), dtype=float)
    contributions[list(active_indices)] = active_contributions
    named_contributions = {
        name: round(float(value), 4)
        for name, value in zip(FEATURE_NAMES, contributions)
    }
    hierarchical_synergy = sum(
        named_contributions[name]
        for name in FEATURE_NAMES
        if name.startswith("mechanics_") or name.endswith("pair_synergy")
    )
    return {
        "model_version": MODEL_VERSION,
        "status": "experimental_v2",
        "team_a": {
            "team_id": team_a,
            "team_name": team_names[team_a],
            "side": "camp1_blue",
            "heroes": [
                {"hero_id": hero_id, "hero_name": hero_names[hero_id]}
                for hero_id in heroes_a
            ],
            "score": round(probability_a * 100.0, 1),
        },
        "team_b": {
            "team_id": team_b,
            "team_name": team_names[team_b],
            "side": "camp2_red",
            "heroes": [
                {"hero_id": hero_id, "hero_name": hero_names[hero_id]}
                for hero_id in heroes_b
            ],
            "score": round((1.0 - probability_a) * 100.0, 1),
        },
        "grouped_contributions_to_team_a_log_odds": {
            "team_strength": named_contributions["team_strength"],
            "hero_familiarity": named_contributions["hero_familiarity"],
            "hierarchical_synergy": round(hierarchical_synergy, 4),
            "counter_advantage": named_contributions["counter_advantage"],
        },
        "detailed_contributions_to_team_a_log_odds": named_contributions,
        "raw_features": {
            name: round(float(value), 6)
            for name, value in zip(FEATURE_NAMES, raw_features)
        },
        "evidence": {key: round(float(value), 4) for key, value in evidence.items()},
        "confidence": confidence(evidence),
        "warning": payload["warning"],
    }


def score(args: argparse.Namespace) -> int:
    result = score_payload(
        load_model(args.model),
        args.team_a,
        args.heroes_a,
        args.team_b,
        args.heroes_b,
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
        print(f"Wrote score: {args.output}")
    else:
        print(serialized, end="")
    return 0


def list_entities(args: argparse.Namespace, entity: str) -> int:
    payload = load_model(args.model)
    rows = payload["team_names"].items() if entity == "teams" else payload["hero_names"].items()
    for identifier, name in sorted(rows, key=lambda row: row[1]):
        print(f"{identifier}\t{name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    train_parser.add_argument("--mechanics", type=Path, default=MECHANICS_PATH)
    train_parser.add_argument("--l2", type=float, default=2.0)
    train_parser.add_argument("--season-decay", type=float, default=0.65)
    train_parser.add_argument("--team-pair-prior", type=float, default=45.0)
    train_parser.add_argument("--model-output", type=Path, default=DEFAULT_MODEL_PATH)
    train_parser.add_argument(
        "--validation-output", type=Path, default=DEFAULT_VALIDATION_PATH
    )
    train_parser.set_defaults(handler=train)

    score_parser = subparsers.add_parser("score")
    score_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
    score_parser.add_argument("--team-a", required=True)
    score_parser.add_argument("--heroes-a", required=True)
    score_parser.add_argument("--team-b", required=True)
    score_parser.add_argument("--heroes-b", required=True)
    score_parser.add_argument("--output", type=Path)
    score_parser.set_defaults(handler=score)

    for command, entity in (("list-teams", "teams"), ("list-heroes", "heroes")):
        entity_parser = subparsers.add_parser(command)
        entity_parser.add_argument("--model", type=Path, default=DEFAULT_MODEL_PATH)
        entity_parser.set_defaults(
            handler=lambda args, selected=entity: list_entities(args, selected)
        )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
