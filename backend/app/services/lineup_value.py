"""Score completed KPL lineups with the maintained lineup-value artifact.

The model orders candidate drafts, but its 0..1 output is intentionally not
exposed as a literal win probability.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = (
    REPO_ROOT
    / "analysis"
    / "artifacts"
    / "lineup_value_model.json"
)
GENERATED_MODEL_ROOT = REPO_ROOT / "analysis" / "outputs"
TACTICAL_ROLES_PATH = REPO_ROOT / "analysis" / "hero_tactical_roles.json"
SUPPORTED_VERSION = "lineup-value-model-v1"

FEATURE_NAMES = (
    "team_strength",
    "hero_familiarity",
    "mechanics_ally_compatibility",
    "mechanics_counter_advantage",
    "league_pair_synergy",
    "team_pair_synergy",
    "historical_counter_advantage",
)
ALLY_RULES = (
    (("mechanic__debuff_armor",), ("damage__physical",)),
    (("mechanic__debuff_magic_defense",), ("damage__magic",)),
    (
        ("mechanic__support_ally_heal",),
        ("condition__channel_or_charge", "condition__low_health_condition"),
    ),
    (
        ("mechanic__support_ally_shield", "mechanic__defense_shield"),
        ("condition__channel_or_charge", "condition__directional"),
    ),
    (
        ("mechanic__support_ally_reposition",),
        ("condition__directional", "condition__distance_scaling"),
    ),
    (
        ("control__strong",),
        ("mechanic__damage_execute", "mechanic__damage_percent_health"),
    ),
)
COUNTER_RULES = (
    (
        ("mechanic__control_anti_mobility",),
        (
            "mechanic__mobility_dash",
            "mechanic__mobility_speed_boost",
            "mechanic__mobility_teleport",
            "mechanic__mobility_wall_traverse",
        ),
    ),
    (
        ("mechanic__defense_cleanse", "mechanic__defense_control_immunity"),
        ("control__strong",),
    ),
    (
        ("mechanic__defense_projectile_block",),
        ("mechanic__vulnerability_projectile_blockable",),
    ),
    (
        ("mechanic__debuff_healing_reduction",),
        (
            "mechanic__support_ally_heal",
            "mechanic__sustain_heal",
            "mechanic__sustain_lifesteal",
        ),
    ),
    (
        ("mechanic__debuff_shield_break",),
        ("mechanic__support_ally_shield", "mechanic__defense_shield"),
    ),
    (
        ("mechanic__damage_execute",),
        ("condition__low_health_condition", "mechanic__sustain_heal"),
    ),
)


@dataclass(frozen=True)
class ResidualStat:
    residual_sum: float = 0.0
    count: float = 0.0

    def effect(self, prior: float) -> float:
        return 4.0 * self.residual_sum / (self.count + prior)

    def evidence(self, prior: float) -> float:
        return self.count / (self.count + prior)


def _average(values: Iterable[float]) -> float:
    materialized = list(values)
    return sum(materialized) / len(materialized) if materialized else 0.0


def _sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


def _rule_density(
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
                if any(source_values.get(name, 0.0) > 0 for name in source_features) and any(
                    target_values.get(name, 0.0) > 0 for name in target_features
                ):
                    hits += 1.0
    pair_count = len(sources) * len(targets) - (
        len(set(sources) & set(targets)) if exclude_self else 0
    )
    return (
        hits / opportunities if opportunities else 0.0,
        known_pairs / pair_count if pair_count else 0.0,
    )


class LineupValueModel:
    """Read-only, in-memory adapter around the lineup-value JSON artifact."""

    def __init__(self, payload: dict[str, Any], tactical_payload: dict[str, Any] | None = None):
        if payload.get("version") != SUPPORTED_VERSION:
            raise ValueError(f"Unsupported lineup value model: {payload.get('version')}")
        model = payload.get("model") or {}
        if tuple(model.get("feature_names") or ()) != FEATURE_NAMES:
            raise ValueError("Lineup value feature schema does not match the runtime")
        self.payload = payload
        self.model = model
        state = payload["state"]
        self.config = {key: float(value) for key, value in state["config"].items()}
        self.ratings = {str(key): float(value) for key, value in state["ratings"].items()}
        self.team_games = {
            str(key): float(value) for key, value in state["team_games"].items()
        }
        self.team_hero = self._decode_stats(state["team_hero"], (str, int))
        self.ally_pair = self._decode_stats(state["ally_pair"], (int, int))
        self.counters = self._decode_stats(state["counters"], (int, int))
        self.team_pair = self._decode_stats(state["team_pair"], (str, int, int))
        self.raw_mechanics = {
            int(hero_id): {name: float(value) for name, value in values.items()}
            for hero_id, values in payload.get("raw_mechanics", {}).items()
        }
        self.hero_names = {
            int(hero_id): str(name) for hero_id, name in payload.get("hero_names", {}).items()
        }
        self.team_names = {
            str(team_id): str(name) for team_id, name in payload.get("team_names", {}).items()
        }
        self.tactical = {
            int(row["hero_id"]): row
            for row in (tactical_payload or {}).get("heroes", [])
            if row.get("hero_id") is not None
        }

    @staticmethod
    def _decode_stats(
        rows: Sequence[Sequence[Any]], converters: Sequence[type]
    ) -> dict[tuple[Any, ...], ResidualStat]:
        result: dict[tuple[Any, ...], ResidualStat] = {}
        key_width = len(converters)
        for row in rows:
            key = tuple(converter(row[index]) for index, converter in enumerate(converters))
            result[key] = ResidualStat(float(row[key_width]), float(row[key_width + 1]))
        return result

    @classmethod
    def from_path(
        cls,
        path: Path = DEFAULT_MODEL_PATH,
        tactical_path: Path = TACTICAL_ROLES_PATH,
    ) -> "LineupValueModel":
        if not path.is_file():
            raise FileNotFoundError(f"Lineup value model is missing: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        tactical_payload = (
            json.loads(tactical_path.read_text(encoding="utf-8"))
            if tactical_path.is_file()
            else None
        )
        return cls(payload, tactical_payload)

    def _stat(self, mapping: dict[tuple[Any, ...], ResidualStat], key: tuple[Any, ...]) -> ResidualStat:
        return mapping.get(key, ResidualStat())

    def team_hero_signal(self, team_id: str, hero_id: int) -> dict[str, float]:
        stat = self._stat(self.team_hero, (str(team_id), int(hero_id)))
        prior = self.config["familiarity_prior"]
        return {
            "effect": stat.effect(prior),
            "evidence": stat.evidence(prior),
            "effective_games": stat.count,
        }

    def composition_profile(self, heroes: Sequence[int]) -> dict[str, float]:
        hero_ids = [int(hero) for hero in heroes]
        classes: dict[str, int] = {}
        roles: dict[str, int] = {}
        hard_cc = 0
        known = 0
        for hero_id in hero_ids:
            tactical = self.tactical.get(hero_id, {})
            if tactical:
                known += 1
            for row in tactical.get("official_classes", []):
                key = str(row.get("key") or "")
                if key:
                    classes[key] = classes.get(key, 0) + 1
            for row in tactical.get("tactical_roles", []):
                key = str(row.get("key") or "")
                if key:
                    roles[key] = roles.get(key, 0) + 1
            if self.raw_mechanics.get(hero_id, {}).get("control__strong", 0.0) > 0:
                hard_cc += 1
        return {
            "hero_count": float(len(hero_ids)),
            "tank_count": float(classes.get("tank", 0)),
            "mage_count": float(classes.get("mage", 0)),
            "assassin_count": float(classes.get("assassin", 0)),
            "frontline_count": float(roles.get("frontline", 0)),
            "primary_engage_count": float(roles.get("primary_engage", 0)),
            "secondary_engage_count": float(roles.get("secondary_engage", 0)),
            "multi_target_control_count": float(roles.get("multi_target_control", 0)),
            "hard_cc_count": float(hard_cc),
            "tactical_coverage": known / len(hero_ids) if hero_ids else 0.0,
        }

    def score(
        self,
        blue_team_id: str,
        blue_heroes: Sequence[int],
        red_team_id: str,
        red_heroes: Sequence[int],
    ) -> dict[str, Any]:
        blue = tuple(int(hero) for hero in blue_heroes)
        red = tuple(int(hero) for hero in red_heroes)
        if len(blue) != 5 or len(set(blue)) != 5:
            raise ValueError("Blue lineup must contain five distinct heroes")
        if len(red) != 5 or len(set(red)) != 5:
            raise ValueError("Red lineup must contain five distinct heroes")
        if set(blue) & set(red):
            raise ValueError("A hero cannot appear on both sides")

        strength = (
            self.ratings.get(str(blue_team_id), 1500.0)
            - self.ratings.get(str(red_team_id), 1500.0)
        ) / 400.0
        familiarity_prior = self.config["familiarity_prior"]
        familiarity_blue = _average(
            self._stat(self.team_hero, (str(blue_team_id), hero)).effect(familiarity_prior)
            for hero in blue
        )
        familiarity_red = _average(
            self._stat(self.team_hero, (str(red_team_id), hero)).effect(familiarity_prior)
            for hero in red
        )
        ally_blue, ally_coverage_blue = _rule_density(
            blue, blue, self.raw_mechanics, ALLY_RULES, exclude_self=True
        )
        ally_red, ally_coverage_red = _rule_density(
            red, red, self.raw_mechanics, ALLY_RULES, exclude_self=True
        )
        mechanics_blue_to_red, counter_coverage_blue = _rule_density(
            blue, red, self.raw_mechanics, COUNTER_RULES, exclude_self=False
        )
        mechanics_red_to_blue, counter_coverage_red = _rule_density(
            red, blue, self.raw_mechanics, COUNTER_RULES, exclude_self=False
        )
        synergy_prior = self.config["synergy_prior"]
        league_pair_blue = _average(
            self._stat(self.ally_pair, tuple(sorted(pair))).effect(synergy_prior)
            for pair in combinations(blue, 2)
        )
        league_pair_red = _average(
            self._stat(self.ally_pair, tuple(sorted(pair))).effect(synergy_prior)
            for pair in combinations(red, 2)
        )
        team_pair_prior = self.config["team_pair_prior"]
        team_pair_blue = _average(
            self._stat(
                self.team_pair, (str(blue_team_id), *tuple(sorted(pair)))
            ).effect(team_pair_prior)
            for pair in combinations(blue, 2)
        )
        team_pair_red = _average(
            self._stat(
                self.team_pair, (str(red_team_id), *tuple(sorted(pair)))
            ).effect(team_pair_prior)
            for pair in combinations(red, 2)
        )
        counter_prior = self.config["counter_prior"]
        historical_counter = _average(
            self._stat(self.counters, (blue_hero, red_hero)).effect(counter_prior)
            for blue_hero in blue
            for red_hero in red
        )
        features = (
            strength,
            familiarity_blue - familiarity_red,
            ally_blue - ally_red,
            mechanics_blue_to_red - mechanics_red_to_blue,
            league_pair_blue - league_pair_red,
            team_pair_blue - team_pair_red,
            historical_counter,
        )
        contributions = {
            name: ((value - float(mean)) / float(scale)) * float(coefficient)
            for name, value, mean, scale, coefficient in zip(
                FEATURE_NAMES,
                features,
                self.model["means"],
                self.model["scales"],
                self.model["coefficients"],
                strict=True,
            )
        }
        logit = float(self.model["intercept"]) + sum(contributions.values())
        advantage = _sigmoid(max(-30.0, min(30.0, logit)))
        grouped = {
            "team_strength": contributions["team_strength"],
            "selected_hero_familiarity": contributions["hero_familiarity"],
            "hero_synergy": sum(
                contributions[name]
                for name in (
                    "mechanics_ally_compatibility",
                    "league_pair_synergy",
                    "team_pair_synergy",
                )
            ),
            "hero_counters": contributions["mechanics_counter_advantage"]
            + contributions["historical_counter_advantage"],
        }
        evidence = {
            "blue_team_games": self.team_games.get(str(blue_team_id), 0.0),
            "red_team_games": self.team_games.get(str(red_team_id), 0.0),
            "hero_familiarity": _average(
                [
                    self._stat(self.team_hero, (str(blue_team_id), hero)).evidence(
                        familiarity_prior
                    )
                    for hero in blue
                ]
                + [
                    self._stat(self.team_hero, (str(red_team_id), hero)).evidence(
                        familiarity_prior
                    )
                    for hero in red
                ]
            ),
            "league_pair_synergy": _average(
                [
                    self._stat(self.ally_pair, tuple(sorted(pair))).evidence(synergy_prior)
                    for pair in combinations(blue, 2)
                ]
                + [
                    self._stat(self.ally_pair, tuple(sorted(pair))).evidence(synergy_prior)
                    for pair in combinations(red, 2)
                ]
            ),
            "team_pair_synergy": _average(
                [
                    self._stat(
                        self.team_pair, (str(blue_team_id), *tuple(sorted(pair)))
                    ).evidence(team_pair_prior)
                    for pair in combinations(blue, 2)
                ]
                + [
                    self._stat(
                        self.team_pair, (str(red_team_id), *tuple(sorted(pair)))
                    ).evidence(team_pair_prior)
                    for pair in combinations(red, 2)
                ]
            ),
            "historical_counter": _average(
                self._stat(self.counters, (blue_hero, red_hero)).evidence(counter_prior)
                for blue_hero in blue
                for red_hero in red
            ),
            "mechanics_ally_coverage": (ally_coverage_blue + ally_coverage_red) / 2.0,
            "mechanics_counter_coverage": (
                counter_coverage_blue + counter_coverage_red
            )
            / 2.0,
        }
        return {
            "blue_advantage": advantage,
            "red_advantage": 1.0 - advantage,
            "features": dict(zip(FEATURE_NAMES, features, strict=True)),
            "contributions": contributions,
            "grouped_contributions": grouped,
            "evidence": evidence,
            "blue_composition": self.composition_profile(blue),
            "red_composition": self.composition_profile(red),
            "interpretation": "relative lineup advantage, not literal win probability",
            "warning": self.payload.get("warning", ""),
        }


_MODEL_CACHE: tuple[Path, int, LineupValueModel] | None = None


def lineup_value_model_path(league_id: str | None = None) -> Path:
    """Prefer a management-built season model, retaining the bundled fallback."""
    if league_id:
        if not all(
            character.isalnum() or character in "-_"
            for character in league_id
        ):
            raise ValueError("Invalid league_id")
        generated = GENERATED_MODEL_ROOT / league_id / "lineup_value_model.json"
        if generated.is_file():
            return generated
    return DEFAULT_MODEL_PATH


def load_lineup_value_model(
    league_id: str | None = None, *, path: Path | None = None
) -> LineupValueModel:
    global _MODEL_CACHE
    path = path or lineup_value_model_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"Lineup value model is missing: {path}")
    modified = path.stat().st_mtime_ns
    if _MODEL_CACHE and _MODEL_CACHE[:2] == (path, modified):
        return _MODEL_CACHE[2]
    model = LineupValueModel.from_path(path)
    _MODEL_CACHE = (path, modified, model)
    return model
