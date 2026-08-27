#!/usr/bin/env python3
"""Historical team, hero, synergy, and counter feature foundations."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np


MODULE_DIR = Path(__file__).resolve().parent
REPO_ROOT = MODULE_DIR.parents[1]
DEFAULT_DB_PATH = REPO_ROOT / "backend" / "data" / "kpl_bp.db"
DEFAULT_ARTIFACT_DIR = REPO_ROOT / "analysis" / "artifacts"
DEFAULT_MODEL_PATH = DEFAULT_ARTIFACT_DIR / "team_score_model.json"
DEFAULT_VALIDATION_PATH = DEFAULT_ARTIFACT_DIR / "validation.json"

FEATURE_NAMES = (
    "team_strength",
    "hero_familiarity",
    "ally_synergy",
    "counter_advantage",
)
MODEL_VERSION = "lineup-value-history-v1"


def clipped_probability(value: float) -> float:
    return min(1.0 - 1e-9, max(1e-9, value))


def sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def elo_probability(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))


@dataclass(frozen=True)
class Battle:
    league_id: str
    match_id: str
    battle_id: str
    start_time: str
    battle_seq: int
    team_a_id: str
    team_a_name: str
    team_b_id: str
    team_b_name: str
    heroes_a: tuple[int, ...]
    heroes_b: tuple[int, ...]
    team_a_won: int


@dataclass
class ResidualStat:
    residual_sum: float = 0.0
    count: float = 0.0

    def effect(self, prior: float) -> float:
        # Multiplying probability residual by four approximates a local logit
        # effect around p=0.5. The prior shrinks sparse keys toward no effect.
        return 4.0 * self.residual_sum / (self.count + prior)

    def evidence(self, prior: float) -> float:
        return self.count / (self.count + prior)

    def update(self, residual: float) -> None:
        self.residual_sum += residual
        self.count += 1.0

    def decay(self, factor: float) -> None:
        self.residual_sum *= factor
        self.count *= factor


@dataclass
class HistoricalState:
    elo_k: float = 24.0
    season_decay: float = 0.65
    familiarity_prior: float = 12.0
    synergy_prior: float = 20.0
    counter_prior: float = 30.0
    ratings: dict[str, float] = field(default_factory=dict)
    team_games: dict[str, float] = field(default_factory=dict)
    team_wins: dict[str, float] = field(default_factory=dict)
    team_hero: dict[tuple[str, int], ResidualStat] = field(default_factory=dict)
    ally_pair: dict[tuple[int, int], ResidualStat] = field(default_factory=dict)
    counters: dict[tuple[int, int], ResidualStat] = field(default_factory=dict)

    def rating(self, team_id: str) -> float:
        return self.ratings.get(team_id, 1500.0)

    def _stat(
        self,
        mapping: dict[Any, ResidualStat],
        key: Any,
    ) -> ResidualStat:
        return mapping.get(key, ResidualStat())

    @staticmethod
    def _average(values: Iterable[float]) -> float:
        materialized = list(values)
        return sum(materialized) / len(materialized) if materialized else 0.0

    def features(
        self,
        team_a_id: str,
        heroes_a: Sequence[int],
        team_b_id: str,
        heroes_b: Sequence[int],
    ) -> tuple[list[float], dict[str, float]]:
        strength = (self.rating(team_a_id) - self.rating(team_b_id)) / 400.0

        familiarity_a = self._average(
            self._stat(self.team_hero, (team_a_id, hero_id)).effect(
                self.familiarity_prior
            )
            for hero_id in heroes_a
        )
        familiarity_b = self._average(
            self._stat(self.team_hero, (team_b_id, hero_id)).effect(
                self.familiarity_prior
            )
            for hero_id in heroes_b
        )
        familiarity = familiarity_a - familiarity_b

        synergy_a = self._average(
            self._stat(self.ally_pair, tuple(sorted(pair))).effect(self.synergy_prior)
            for pair in combinations(heroes_a, 2)
        )
        synergy_b = self._average(
            self._stat(self.ally_pair, tuple(sorted(pair))).effect(self.synergy_prior)
            for pair in combinations(heroes_b, 2)
        )
        synergy = synergy_a - synergy_b

        counter = self._average(
            self._stat(self.counters, (hero_a, hero_b)).effect(self.counter_prior)
            for hero_a in heroes_a
            for hero_b in heroes_b
        )

        evidence = {
            "team_a_games": float(self.team_games.get(team_a_id, 0)),
            "team_b_games": float(self.team_games.get(team_b_id, 0)),
            "hero_familiarity": self._average(
                [
                    self._stat(self.team_hero, (team_a_id, hero_id)).evidence(
                        self.familiarity_prior
                    )
                    for hero_id in heroes_a
                ]
                + [
                    self._stat(self.team_hero, (team_b_id, hero_id)).evidence(
                        self.familiarity_prior
                    )
                    for hero_id in heroes_b
                ]
            ),
            "ally_synergy": self._average(
                [
                    self._stat(self.ally_pair, tuple(sorted(pair))).evidence(
                        self.synergy_prior
                    )
                    for pair in combinations(heroes_a, 2)
                ]
                + [
                    self._stat(self.ally_pair, tuple(sorted(pair))).evidence(
                        self.synergy_prior
                    )
                    for pair in combinations(heroes_b, 2)
                ]
            ),
            "counter_advantage": self._average(
                self._stat(self.counters, (hero_a, hero_b)).evidence(
                    self.counter_prior
                )
                for hero_a in heroes_a
                for hero_b in heroes_b
            ),
        }
        return [strength, familiarity, synergy, counter], evidence

    def advance_season(self) -> None:
        """Reduce stale patch and roster evidence at a season boundary."""
        factor = self.season_decay
        self.ratings = {
            team_id: 1500.0 + factor * (rating - 1500.0)
            for team_id, rating in self.ratings.items()
        }
        self.team_games = {
            team_id: games * factor for team_id, games in self.team_games.items()
        }
        self.team_wins = {
            team_id: wins * factor for team_id, wins in self.team_wins.items()
        }
        for mapping in (self.team_hero, self.ally_pair, self.counters):
            for stat in mapping.values():
                stat.decay(factor)

    def update(self, battle: Battle) -> None:
        expected_a = elo_probability(
            self.rating(battle.team_a_id), self.rating(battle.team_b_id)
        )
        residual_a = float(battle.team_a_won) - expected_a
        residual_b = -residual_a

        for hero_id in battle.heroes_a:
            self.team_hero.setdefault(
                (battle.team_a_id, hero_id), ResidualStat()
            ).update(residual_a)
        for hero_id in battle.heroes_b:
            self.team_hero.setdefault(
                (battle.team_b_id, hero_id), ResidualStat()
            ).update(residual_b)

        for pair in combinations(battle.heroes_a, 2):
            self.ally_pair.setdefault(tuple(sorted(pair)), ResidualStat()).update(
                residual_a
            )
        for pair in combinations(battle.heroes_b, 2):
            self.ally_pair.setdefault(tuple(sorted(pair)), ResidualStat()).update(
                residual_b
            )

        for hero_a in battle.heroes_a:
            for hero_b in battle.heroes_b:
                self.counters.setdefault((hero_a, hero_b), ResidualStat()).update(
                    residual_a
                )
                self.counters.setdefault((hero_b, hero_a), ResidualStat()).update(
                    residual_b
                )

        old_a = self.rating(battle.team_a_id)
        old_b = self.rating(battle.team_b_id)
        self.ratings[battle.team_a_id] = old_a + self.elo_k * residual_a
        self.ratings[battle.team_b_id] = old_b + self.elo_k * residual_b
        for team_id, won in (
            (battle.team_a_id, battle.team_a_won),
            (battle.team_b_id, 1 - battle.team_a_won),
        ):
            self.team_games[team_id] = self.team_games.get(team_id, 0) + 1
            self.team_wins[team_id] = self.team_wins.get(team_id, 0) + won

    def to_dict(self) -> dict[str, Any]:
        def encoded_rows(mapping: dict[tuple[Any, ...], ResidualStat]) -> list[list[Any]]:
            return [
                [*key, round(stat.residual_sum, 10), round(stat.count, 10)]
                for key, stat in sorted(mapping.items())
            ]

        return {
            "config": {
                "elo_k": self.elo_k,
                "season_decay": self.season_decay,
                "familiarity_prior": self.familiarity_prior,
                "synergy_prior": self.synergy_prior,
                "counter_prior": self.counter_prior,
            },
            "ratings": {key: round(value, 8) for key, value in self.ratings.items()},
            "team_games": self.team_games,
            "team_wins": self.team_wins,
            "team_hero": encoded_rows(self.team_hero),
            "ally_pair": encoded_rows(self.ally_pair),
            "counters": encoded_rows(self.counters),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HistoricalState":
        state = cls(**payload["config"])
        state.ratings = {str(k): float(v) for k, v in payload["ratings"].items()}
        state.team_games = {
            str(k): float(v) for k, v in payload["team_games"].items()
        }
        state.team_wins = {
            str(k): float(v) for k, v in payload["team_wins"].items()
        }
        for row in payload["team_hero"]:
            state.team_hero[(str(row[0]), int(row[1]))] = ResidualStat(
                float(row[2]), float(row[3])
            )
        for row in payload["ally_pair"]:
            state.ally_pair[(int(row[0]), int(row[1]))] = ResidualStat(
                float(row[2]), float(row[3])
            )
        for row in payload["counters"]:
            state.counters[(int(row[0]), int(row[1]))] = ResidualStat(
                float(row[2]), float(row[3])
            )
        return state


def load_battles(
    db_path: Path,
) -> tuple[list[Battle], dict[str, str], dict[int, str]]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Database not found: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT
                b.league_id,
                b.match_id,
                b.battle_id,
                COALESCE(m.start_time, '') AS start_time,
                b.battle_seq,
                b.win_camp,
                p.camp,
                p.team_id,
                p.team_name,
                p.hero_id,
                p.hero_name
            FROM battles b
            JOIN matches m ON m.match_id = b.match_id
            JOIN battle_players p ON p.battle_id = b.battle_id
            WHERE b.win_camp IN (1, 2)
              AND p.camp IN (1, 2)
              AND p.hero_id > 0
              AND p.team_id <> ''
            ORDER BY m.start_time, b.match_id, b.battle_seq, p.camp, p.hero_id
            """
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, dict[str, Any]] = {}
    team_names: dict[str, str] = {}
    hero_names: dict[int, str] = {}
    for row in rows:
        battle_id = str(row["battle_id"])
        record = grouped.setdefault(
            battle_id,
            {
                "league_id": str(row["league_id"]),
                "match_id": str(row["match_id"]),
                "battle_id": battle_id,
                "start_time": str(row["start_time"]),
                "battle_seq": int(row["battle_seq"]),
                "win_camp": int(row["win_camp"]),
                "teams": {},
                "heroes": defaultdict(list),
            },
        )
        camp = int(row["camp"])
        team_id = str(row["team_id"])
        team_name = str(row["team_name"] or team_id)
        hero_id = int(row["hero_id"])
        hero_name = str(row["hero_name"] or hero_id)
        record["teams"][camp] = (team_id, team_name)
        record["heroes"][camp].append(hero_id)
        team_names[team_id] = team_name
        hero_names[hero_id] = hero_name

    battles: list[Battle] = []
    for record in grouped.values():
        if set(record["teams"]) != {1, 2}:
            continue
        heroes_a = tuple(sorted(set(record["heroes"][1])))
        heroes_b = tuple(sorted(set(record["heroes"][2])))
        if len(heroes_a) != 5 or len(heroes_b) != 5:
            continue
        team_a = record["teams"][1]
        team_b = record["teams"][2]
        battles.append(
            Battle(
                league_id=record["league_id"],
                match_id=record["match_id"],
                battle_id=record["battle_id"],
                start_time=record["start_time"],
                battle_seq=record["battle_seq"],
                team_a_id=team_a[0],
                team_a_name=team_a[1],
                team_b_id=team_b[0],
                team_b_name=team_b[1],
                heroes_a=heroes_a,
                heroes_b=heroes_b,
                team_a_won=1 if record["win_camp"] == 1 else 0,
            )
        )
    battles.sort(
        key=lambda battle: (
            battle.start_time,
            battle.match_id,
            battle.battle_seq,
            battle.battle_id,
        )
    )
    return battles, team_names, hero_names


def build_prequential_features(
    battles: Sequence[Battle],
    *,
    state: HistoricalState | None = None,
) -> tuple[np.ndarray, np.ndarray, list[str], HistoricalState]:
    history = state or HistoricalState()
    features: list[list[float]] = []
    outcomes: list[int] = []
    leagues: list[str] = []
    previous_league: str | None = None
    for battle in battles:
        if previous_league is not None and battle.league_id != previous_league:
            history.advance_season()
        row, _evidence = history.features(
            battle.team_a_id,
            battle.heroes_a,
            battle.team_b_id,
            battle.heroes_b,
        )
        features.append(row)
        outcomes.append(battle.team_a_won)
        leagues.append(battle.league_id)
        history.update(battle)
        previous_league = battle.league_id
    return (
        np.asarray(features, dtype=float),
        np.asarray(outcomes, dtype=float),
        leagues,
        history,
    )


def fit_logistic(
    features: np.ndarray,
    outcomes: np.ndarray,
    *,
    l2: float = 2.0,
    max_iterations: int = 100,
) -> dict[str, Any]:
    if len(features) != len(outcomes) or len(outcomes) == 0:
        raise ValueError("Training data must be nonempty and aligned")
    means = features.mean(axis=0)
    scales = features.std(axis=0)
    scales = np.where(scales < 1e-9, 1.0, scales)
    normalized = (features - means) / scales
    design = np.column_stack([np.ones(len(normalized)), normalized])
    coefficients = np.zeros(design.shape[1], dtype=float)
    regularizer = np.eye(design.shape[1], dtype=float) * l2
    regularizer[0, 0] = 0.0

    for _iteration in range(max_iterations):
        logits = np.clip(design @ coefficients, -30.0, 30.0)
        probabilities = 1.0 / (1.0 + np.exp(-logits))
        weights = np.maximum(probabilities * (1.0 - probabilities), 1e-8)
        gradient = design.T @ (probabilities - outcomes) + regularizer @ coefficients
        hessian = design.T @ (design * weights[:, None]) + regularizer
        step = np.linalg.solve(hessian, gradient)
        coefficients -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break

    return {
        "feature_names": list(FEATURE_NAMES[: features.shape[1]]),
        "means": means.tolist(),
        "scales": scales.tolist(),
        "intercept": float(coefficients[0]),
        "coefficients": coefficients[1:].tolist(),
        "l2": l2,
    }


def predict_probabilities(model: dict[str, Any], features: np.ndarray) -> np.ndarray:
    means = np.asarray(model["means"], dtype=float)
    scales = np.asarray(model["scales"], dtype=float)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    normalized = (features - means) / scales
    logits = float(model["intercept"]) + normalized @ coefficients
    return 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))


def auc_score(outcomes: np.ndarray, probabilities: np.ndarray) -> float | None:
    positives = int(outcomes.sum())
    negatives = len(outcomes) - positives
    if positives == 0 or negatives == 0:
        return None
    order = np.argsort(probabilities)
    ranks = np.empty(len(probabilities), dtype=float)
    start = 0
    while start < len(order):
        end = start + 1
        while (
            end < len(order)
            and probabilities[order[end]] == probabilities[order[start]]
        ):
            end += 1
        ranks[order[start:end]] = (start + 1 + end) / 2.0
        start = end
    positive_rank_sum = float(ranks[outcomes == 1].sum())
    return (positive_rank_sum - positives * (positives + 1) / 2.0) / (
        positives * negatives
    )


def expected_calibration_error(
    outcomes: np.ndarray,
    probabilities: np.ndarray,
    bins: int = 10,
) -> float:
    total = len(outcomes)
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        mask = (probabilities >= low) & (
            probabilities <= high if index == bins - 1 else probabilities < high
        )
        count = int(mask.sum())
        if count:
            error += count / total * abs(
                float(probabilities[mask].mean()) - float(outcomes[mask].mean())
            )
    return error


def metrics(outcomes: np.ndarray, probabilities: np.ndarray) -> dict[str, Any]:
    clipped = np.clip(probabilities, 1e-9, 1.0 - 1e-9)
    auc = auc_score(outcomes, clipped)
    return {
        "battle_count": len(outcomes),
        "win_rate": round(float(outcomes.mean()), 6),
        "log_loss": round(
            float(-(outcomes * np.log(clipped) + (1 - outcomes) * np.log(1 - clipped)).mean()),
            6,
        ),
        "brier_score": round(float(np.mean((clipped - outcomes) ** 2)), 6),
        "accuracy": round(float(np.mean((clipped >= 0.5) == outcomes)), 6),
        "auc": round(float(auc), 6) if auc is not None else None,
        "calibration_error": round(
            expected_calibration_error(outcomes, clipped), 6
        ),
        "mean_prediction": round(float(clipped.mean()), 6),
    }


def rolling_validation(
    features: np.ndarray,
    outcomes: np.ndarray,
    leagues: Sequence[str],
) -> dict[str, Any]:
    ordered_leagues = list(dict.fromkeys(leagues))
    folds: list[dict[str, Any]] = []
    pooled: dict[str, list[np.ndarray]] = defaultdict(list)
    league_array = np.asarray(leagues)
    model_specs = {
        "elo_only": (0,),
        "elo_plus_familiarity": (0, 1),
        "elo_plus_synergy": (0, 2),
        "elo_plus_counter": (0, 3),
        "full": (0, 1, 2, 3),
    }

    for test_league in ordered_leagues[1:]:
        test_position = ordered_leagues.index(test_league)
        train_leagues = ordered_leagues[:test_position]
        train_mask = np.isin(league_array, train_leagues)
        test_mask = league_array == test_league
        train_x = features[train_mask]
        train_y = outcomes[train_mask]
        test_x = features[test_mask]
        test_y = outcomes[test_mask]

        prior_probability = clipped_probability(float(train_y.mean()))
        intercept_predictions = np.full(len(test_y), prior_probability)
        model_predictions = {"intercept_only": intercept_predictions}
        for name, columns in model_specs.items():
            selected_train = train_x[:, columns]
            selected_test = test_x[:, columns]
            fitted = fit_logistic(selected_train, train_y)
            model_predictions[name] = predict_probabilities(fitted, selected_test)
        folds.append(
            {
                "test_league_id": test_league,
                "training_league_ids": train_leagues,
                "models": {
                    name: metrics(test_y, predictions)
                    for name, predictions in model_predictions.items()
                },
            }
        )
        pooled["outcomes"].append(test_y)
        for name, predictions in model_predictions.items():
            pooled[name].append(predictions)

    pooled_outcomes = np.concatenate(pooled["outcomes"])
    aggregate = {
        name: metrics(pooled_outcomes, np.concatenate(pooled[name]))
        for name in ("intercept_only", *model_specs.keys())
    }
    aggregate["full_vs_elo"] = {
        "log_loss_improvement": round(
            aggregate["elo_only"]["log_loss"] - aggregate["full"]["log_loss"],
            6,
        ),
        "brier_improvement": round(
            aggregate["elo_only"]["brier_score"]
            - aggregate["full"]["brier_score"],
            6,
        ),
        "auc_improvement": round(
            aggregate["full"]["auc"] - aggregate["elo_only"]["auc"], 6
        ),
    }
    improved_folds = sum(
        fold["models"]["full"]["log_loss"]
        < fold["models"]["elo_only"]["log_loss"]
        for fold in folds
    )
    release_assessment = {
        "verdict": "promising_poc_not_release_ready",
        "full_model_log_loss_better_than_elo": aggregate["full_vs_elo"][
            "log_loss_improvement"
        ]
        > 0,
        "full_model_brier_better_than_elo": aggregate["full_vs_elo"][
            "brier_improvement"
        ]
        > 0,
        "held_out_seasons_with_log_loss_improvement": improved_folds,
        "held_out_season_count": len(folds),
        "minimum_material_log_loss_gain_met": aggregate["full_vs_elo"][
            "log_loss_improvement"
        ]
        >= 0.005,
        "reason": "The hero components add a small pooled gain, but it is below the 0.005 material-improvement gate and is not stable in every held-out season.",
    }
    return {
        "method": "rolling season holdout; all features are prequential",
        "folds": folds,
        "aggregate": aggregate,
        "release_assessment": release_assessment,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def train(args: argparse.Namespace) -> int:
    battles, team_names, hero_names = load_battles(args.db)
    if not battles:
        raise ValueError("No complete labeled 5v5 battles found")
    initial_state = HistoricalState(season_decay=args.season_decay)
    features, outcomes, leagues, final_state = build_prequential_features(
        battles, state=initial_state
    )
    validation = rolling_validation(features, outcomes, leagues)
    model = fit_logistic(features, outcomes, l2=args.l2)
    generated_at = datetime.now(timezone.utc).isoformat()
    model_payload = {
        "version": MODEL_VERSION,
        "generated_at": generated_at,
        "source": {
            "database": str(args.db.resolve()),
            "database_open_mode": "read_only",
            "battle_count": len(battles),
            "league_ids": list(dict.fromkeys(leagues)),
            "first_battle_time": battles[0].start_time,
            "last_battle_time": battles[-1].start_time,
        },
        "model": model,
        "state": final_state.to_dict(),
        "team_names": team_names,
        "hero_names": {str(key): value for key, value in hero_names.items()},
        "interpretation": {
            "team_a_score": "100 * estimated matchup probability",
            "team_b_score": "100 - team_a_score",
            "warning": "Proof of concept; do not present as a validated win probability until release gates pass.",
        },
    }
    validation_payload = {
        "version": MODEL_VERSION,
        "generated_at": generated_at,
        "feature_names": list(FEATURE_NAMES),
        **validation,
    }
    write_json(args.model_output, model_payload)
    write_json(args.validation_output, validation_payload)
    print(json.dumps(validation_payload["aggregate"], indent=2))
    print(f"Wrote model: {args.model_output}")
    print(f"Wrote validation: {args.validation_output}")
    return 0


def load_model(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != MODEL_VERSION:
        raise ValueError(f"Unsupported model version: {payload.get('version')}")
    return payload


def resolve_team(value: str, names: dict[str, str]) -> str:
    if value in names:
        return value
    matches = [team_id for team_id, name in names.items() if name == value]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous team: {value!r}")
    return matches[0]


def resolve_heroes(value: str, names: dict[int, str]) -> list[int]:
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    resolved: list[int] = []
    for token in tokens:
        if token.isdigit() and int(token) in names:
            resolved.append(int(token))
            continue
        matches = [hero_id for hero_id, name in names.items() if name == token]
        if len(matches) != 1:
            raise ValueError(f"Unknown or ambiguous hero: {token!r}")
        resolved.append(matches[0])
    if len(resolved) != 5 or len(set(resolved)) != 5:
        raise ValueError("Each side must contain exactly five distinct heroes")
    return resolved


def confidence_label(evidence: dict[str, float]) -> str:
    team_evidence = min(evidence["team_a_games"], evidence["team_b_games"])
    pair_evidence = (
        evidence["hero_familiarity"]
        + evidence["ally_synergy"]
        + evidence["counter_advantage"]
    ) / 3.0
    if team_evidence >= 60 and pair_evidence >= 0.45:
        return "high"
    if team_evidence >= 20 and pair_evidence >= 0.2:
        return "medium"
    return "low"


def score_payload(
    payload: dict[str, Any],
    team_a_value: str,
    heroes_a_value: str,
    team_b_value: str,
    heroes_b_value: str,
) -> dict[str, Any]:
    team_names = {str(k): str(v) for k, v in payload["team_names"].items()}
    hero_names = {int(k): str(v) for k, v in payload["hero_names"].items()}
    team_a = resolve_team(team_a_value, team_names)
    team_b = resolve_team(team_b_value, team_names)
    if team_a == team_b:
        raise ValueError("The two teams must be different")
    heroes_a = resolve_heroes(heroes_a_value, hero_names)
    heroes_b = resolve_heroes(heroes_b_value, hero_names)
    if set(heroes_a) & set(heroes_b):
        raise ValueError("A hero cannot appear on both sides of one battle")

    state = HistoricalState.from_dict(payload["state"])
    raw_features, evidence = state.features(team_a, heroes_a, team_b, heroes_b)
    model = payload["model"]
    feature_array = np.asarray([raw_features], dtype=float)
    probability_a = float(predict_probabilities(model, feature_array)[0])
    means = np.asarray(model["means"], dtype=float)
    scales = np.asarray(model["scales"], dtype=float)
    coefficients = np.asarray(model["coefficients"], dtype=float)
    contributions = ((feature_array[0] - means) / scales) * coefficients

    return {
        "model_version": payload["version"],
        "status": "proof_of_concept",
        "team_a": {
            "team_id": team_a,
            "team_name": team_names[team_a],
            "heroes": [
                {"hero_id": hero_id, "hero_name": hero_names[hero_id]}
                for hero_id in heroes_a
            ],
            "score": round(100.0 * probability_a, 1),
            "side": "camp1_blue",
        },
        "team_b": {
            "team_id": team_b,
            "team_name": team_names[team_b],
            "heroes": [
                {"hero_id": hero_id, "hero_name": hero_names[hero_id]}
                for hero_id in heroes_b
            ],
            "score": round(100.0 * (1.0 - probability_a), 1),
            "side": "camp2_red",
        },
        "component_contributions_to_team_a_log_odds": {
            name: round(float(value), 4)
            for name, value in zip(FEATURE_NAMES, contributions)
        },
        "raw_features": {
            name: round(float(value), 6)
            for name, value in zip(FEATURE_NAMES, raw_features)
        },
        "evidence": {
            key: round(float(value), 4) for key, value in evidence.items()
        },
        "confidence": confidence_label(evidence),
        "warning": payload["interpretation"]["warning"],
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
    if entity == "teams":
        rows = sorted(payload["team_names"].items(), key=lambda row: row[1])
    else:
        rows = sorted(
            ((int(key), value) for key, value in payload["hero_names"].items()),
            key=lambda row: row[1],
        )
    for identifier, name in rows:
        print(f"{identifier}\t{name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train", help="Train and validate the historical model")
    train_parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    train_parser.add_argument("--l2", type=float, default=2.0)
    train_parser.add_argument("--season-decay", type=float, default=0.65)
    train_parser.add_argument(
        "--model-output", type=Path, default=DEFAULT_MODEL_PATH
    )
    train_parser.add_argument(
        "--validation-output", type=Path, default=DEFAULT_VALIDATION_PATH
    )
    train_parser.set_defaults(handler=train)

    score_parser = subparsers.add_parser("score", help="Score one 5v5 matchup")
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
