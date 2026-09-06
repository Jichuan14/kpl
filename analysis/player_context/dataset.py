"""Versioned context-table dataset and candidate-level familiarity features."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Iterable

import numpy as np
from numpy.typing import NDArray

from .history import ROLE_IDS, TemporalContextBuilder
from .roles import open_role_probabilities


CONTEXT_DATASET_VERSION = "personalized_context_dataset_v1"
FAMILIARITY_FEATURE_NAMES = (
    "own_affinity", "opponent_affinity", "own_role_fit", "opponent_role_fit",
    "own_previous_usage", "opponent_previous_usage", "coverage",
    "own_no_open_role", "opponent_no_open_role",
)


def _coverage(context: dict[str, Any], team_index: int) -> float:
    values = []
    for role in range(5):
        value = sum(
            component["weight"] * history["reliability"]
            for component, history in zip(context["rosters"][team_index][role], context["histories"][team_index][role], strict=True)
        )
        values.append(value)
    return float(np.clip(np.mean(values), 0.0, 1.0))


def candidate_familiarity_features(
    context: dict[str, Any], *, own_picks: list[int], opponent_picks: list[int],
    own_previous: Iterable[int] = (), opponent_previous: Iterable[int] = (), hero_ids: list[int],
) -> NDArray[np.float32]:
    hero_to_index = {hero: index for index, hero in enumerate(hero_ids)}
    prior = np.asarray(context["hero_role_prior"], dtype=np.float64)
    own_open = open_role_probabilities([hero_to_index[h] for h in own_picks if h in hero_to_index], prior)
    opponent_open = open_role_probabilities([hero_to_index[h] for h in opponent_picks if h in hero_to_index], prior)
    own_norm = own_open / own_open.sum() if own_open.sum() else own_open
    opponent_norm = opponent_open / opponent_open.sum() if opponent_open.sum() else opponent_open
    familiarity = np.asarray(context["familiarity"], dtype=np.float64)  # [2,5,H]
    own_affinity = own_norm @ familiarity[0]
    opponent_affinity = opponent_norm @ familiarity[1]
    own_fit = prior @ own_open
    opponent_fit = prior @ opponent_open
    own_previous_set, opponent_previous_set = set(own_previous), set(opponent_previous)
    own_coverage, opponent_coverage = _coverage(context, 0), _coverage(context, 1)
    combined_coverage = (own_coverage + opponent_coverage) / 2
    return np.asarray([
        [own_affinity[index], opponent_affinity[index], own_fit[index], opponent_fit[index],
         float(hero in own_previous_set), float(hero in opponent_previous_set), combined_coverage,
         float(own_open.sum() == 0), float(opponent_open.sum() == 0)]
        for index, hero in enumerate(hero_ids)
    ], dtype=np.float32)


def build_context_table(
    builder: TemporalContextBuilder, decisions: Iterable[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    contexts: dict[str, dict[str, Any]] = {}
    rows = []
    cache: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in decisions:
        cutoff = date.fromisoformat(str(row["event_date"])[:10])
        key = (str(row["acting_team_id"]), str(row["opponent_team_id"]), cutoff.isoformat())
        context = cache.get(key)
        if context is None:
            context = builder.build(key[0], key[1], cutoff)
            cache[key] = context
        contexts[context["context_id"]] = context
        features = candidate_familiarity_features(
            context, own_picks=[int(v) for v in row.get("current_team_picks", [])],
            opponent_picks=[int(v) for v in row.get("current_opponent_picks", [])],
            own_previous=row.get("team_used_in_previous_battles", []),
            opponent_previous=row.get("opponent_used_in_previous_battles", []), hero_ids=list(builder.hero_ids),
        )
        rows.append({"match_id": str(row["match_id"]), "battle_id": str(row["battle_id"]),
                     "bp_order": int(row["bp_order"]), "context_id": context["context_id"], "candidate_features": features})
    return contexts, rows


def context_table_identity(contexts: dict[str, dict[str, Any]]) -> str:
    identities = {key: value["identity"] for key, value in sorted(contexts.items())}
    return hashlib.sha256(json.dumps({"version": CONTEXT_DATASET_VERSION, "contexts": identities}, sort_keys=True).encode()).hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(value, tmp, ensure_ascii=False, separators=(",", ":"))
        tmp.write("\n"); temporary = Path(tmp.name)
    temporary.replace(path)
