"""Load season draft models and run probability-backed BP simulations."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
from pathlib import Path
from typing import Any

import numpy as np

from app.services.analysis_pipeline import ANALYSIS_DIR, OUTPUT_ROOT, REPO_ROOT
from app.services.draft_calibration import (
    CALIBRATION_FILENAME,
    CalibrationResolution,
    GAME_AVAILABILITY_POLICY_ID,
    LEGACY_POLICY_ID,
    candidate_policy_fingerprint,
    masked_softmax,
    resolve_calibration,
    semantic_model_fingerprint,
)
from app.services.sequence_model_runtime import (
    ACTION_INDEX,
    SIDE_INDEX,
    prepare_sequence_parameters,
    sequence_logits,
)
from app.services.personalized_model_runtime import personalized_logits, prepare_personalized_parameters
from app.services.player_draft_context import candidate_features as personalized_candidate_features, load_context_snapshot

_CACHE: dict[Path, tuple[tuple[int, int], dict[str, Any]]] = {}
_LEARNABLE_CACHE: dict[Path, tuple[int, dict[str, Any]]] = {}
_SEQUENCE_CACHE: dict[Path, tuple[tuple[int, int, int, str], dict[str, Any]]] = {}
_PERSONALIZED_CACHE: dict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = {}
_TEAM_TENDENCY_CACHE: dict[
    Path,
    tuple[
        int,
        dict[tuple[str, str, str, str, int, str], dict[int, dict[str, Any]]],
    ],
] = {}
LEGACY_SPECIALTY_FEATURES_PATH = ANALYSIS_DIR / "hero_specialty_vectors_thermometer.json"
DRAFT_FEATURES_PATH = ANALYSIS_DIR / "hero_draft_feature_vectors.json"
OFFICIAL_HEROLIST_PATH = REPO_ROOT / "knowledge" / "sources" / "official" / "herolist.json"
# Tencent herolist `roles` uses 1–5 for 对抗/打野/中路/发育/游走.
# KPL `hero_positions` uses 6/5/2/7/4 for the same lanes.
TENCENT_LANE_TO_POSITION = {
    1: 6,
    2: 5,
    3: 2,
    4: 7,
    5: 4,
}
# Public requests and AI tool calls use this fixed, lowest supported rollout
# count.  Keep the low-level ``simulate`` function parameterized for offline
# analysis and deterministic unit tests, but do not expose that control at an
# HTTP or model-tool boundary.
FIXED_ROLLOUTS = 50
HERO_POSITION_FALLBACKS = {
    109: [2],  # 妲己 · 中路
    114: [4],  # 刘禅 · 游走
    115: [2],  # 高渐离 · 中路
    129: [5],  # 典韦 · 打野
    154: [6],  # 花木兰 · 对抗路
    513: [2],  # 上官婉儿 · 中路
    524: [7],  # 蒙犽 · 发育路
    528: [5],  # 澜 · 打野
}
POSITION_LANES = {
    2: "mid",
    4: "roam",
    5: "jungle",
    6: "clash",
    7: "farm",
}


def model_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "draft_model.json"


def learnable_model_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "learnable_draft_choice_model.json"


def sequence_model_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "sequence_draft_choice_model.json"


def sequence_calibration_path(league_id: str) -> Path:
    return sequence_model_path(league_id).with_name(CALIBRATION_FILENAME)


def personalized_model_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "personalized_draft_choice_model.json"


def personalized_context_path(league_id: str) -> Path:
    return OUTPUT_ROOT / league_id / "player_draft_context.json"


def personalized_calibration_path(league_id: str) -> Path:
    return OUTPUT_ROOT / league_id / "personalized_draft_probability_calibration.json"


def load_personalized_model(league_id: str) -> dict[str, Any]:
    """Load the production sequence + familiarity policy."""
    path=personalized_model_path(league_id)
    if not path.is_file(): raise FileNotFoundError(f"No personalized draft model has been generated for {league_id}")
    context_path=personalized_context_path(league_id)
    calibration_path=personalized_calibration_path(league_id)
    signature=(path.stat().st_mtime_ns,context_path.stat().st_mtime_ns if context_path.is_file() else -1,calibration_path.stat().st_mtime_ns if calibration_path.is_file() else -1);cached=_PERSONALIZED_CACHE.get(path)
    if cached and cached[0]==signature: return cached[1]
    model=json.loads(path.read_text(encoding="utf-8"))
    if str(model.get("base_artifact",{}).get("target_season"))!=league_id: raise ValueError("Personalized base target season mismatch")
    feature_matrix=np.asarray(model.get("hero_feature_matrix",[]),dtype=np.float32);base=dict(model["base_artifact"])
    hero_ids=[int(v) for v in base.get("hero_ids",[])];team_ids=[str(v) for v in base.get("team_ids",[])]
    if feature_matrix.shape!=(len(hero_ids),len(base.get("feature_names",[]))): raise ValueError("Personalized feature matrix is malformed")
    model["_hero_ids"]=hero_ids;model["_hero_to_index"]={v:i for i,v in enumerate(hero_ids)};model["_team_to_index"]={v:i+1 for i,v in enumerate(team_ids)}
    model["_prepared"]=prepare_sequence_parameters(base,feature_matrix);model["_personalized_prepared"]=prepare_personalized_parameters(model);model["_feature_matrix"]=feature_matrix
    model["team_training_decisions"]=base.get("team_training_decisions",{});model["generated_at"]=model.get("generated_at");model["_personalization_reason"]="runtime_context_snapshot_unavailable"
    model["_calibration_path"]=calibration_path
    if context_path.is_file():
        model["_context_snapshot"]=load_context_snapshot(context_path,hero_ids);model["_personalization_reason"]="context_available"
    _PERSONALIZED_CACHE[path]=(signature,model);return model


def feature_space_path(league_id: str) -> Path:
    if not league_id or not all(character.isalnum() or character in "-_" for character in league_id):
        raise ValueError("Invalid league id")
    return OUTPUT_ROOT / league_id / "learned_hero_feature_space.json"


def feature_artifact_path(model: dict[str, Any]) -> Path:
    """Resolve the model's versioned feature artifact without accepting paths."""
    filename = str(model.get("feature_artifact") or LEGACY_SPECIALTY_FEATURES_PATH.name)
    supported = {
        LEGACY_SPECIALTY_FEATURES_PATH.name: LEGACY_SPECIALTY_FEATURES_PATH,
        DRAFT_FEATURES_PATH.name: DRAFT_FEATURES_PATH,
    }
    try:
        return supported[filename]
    except KeyError as exc:
        raise ValueError(f"Unsupported learnable feature artifact: {filename}") from exc


def parse_official_lane_ids(value: Any) -> list[int]:
    """Parse Tencent `roles` (`majority|secondary`) into KPL position IDs."""
    if value in (None, "", 0, "0"):
        return []
    positions: list[int] = []
    for part in str(value).replace(",", "|").split("|"):
        token = part.strip()
        if not token:
            continue
        try:
            lane_id = int(token)
        except ValueError:
            continue
        position = TENCENT_LANE_TO_POSITION.get(lane_id)
        if position and position not in positions:
            positions.append(position)
    return positions


def official_hero_positions(path: Path | None = None) -> dict[int, list[int]]:
    """Majority and optional secondary lanes from the official herolist catalog."""
    source = path or OFFICIAL_HEROLIST_PATH
    if not source.is_file():
        return {}
    catalog = json.loads(source.read_text(encoding="utf-8"))
    mapping: dict[int, list[int]] = {}
    if not isinstance(catalog, list):
        return mapping
    for row in catalog:
        if not isinstance(row, dict):
            continue
        hero_id = int(row.get("ename") or 0)
        if hero_id <= 0:
            continue
        positions = parse_official_lane_ids(row.get("roles"))
        if positions:
            mapping[hero_id] = positions
    return mapping


def apply_official_lane_eligibility(
    model: dict[str, Any],
    official_positions: dict[int, list[int]] | None = None,
) -> dict[str, Any]:
    """Use official majority/secondary lanes for hero-table role eligibility."""
    mapping = official_hero_positions() if official_positions is None else official_positions
    names = model.get("hero_names") or {}
    positions = {
        str(hero_id): [int(position) for position in values]
        for hero_id, values in (model.get("hero_positions") or {}).items()
    }
    for hero_id, lanes in mapping.items():
        key = str(hero_id)
        if key in names or key in positions:
            positions[key] = [int(position) for position in lanes]
    model["hero_positions"] = positions
    model["role_ids"] = sorted(
        {
            int(role_id)
            for role_id in model.get("role_ids") or []
        }
        | {
            int(position)
            for values in positions.values()
            for position in values
        }
    )
    return model


def load_model(league_id: str) -> dict[str, Any]:
    path = model_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"No draft model has been generated for {league_id}")
    modified = path.stat().st_mtime_ns
    official_modified = (
        OFFICIAL_HEROLIST_PATH.stat().st_mtime_ns if OFFICIAL_HEROLIST_PATH.is_file() else 0
    )
    cache_key = (modified, official_modified)
    cached = _CACHE.get(path)
    if cached and cached[0] == cache_key:
        return cached[1]
    with path.open(encoding="utf-8") as source:
        model = json.load(source)
    if model.get("schema_version") != 1:
        raise ValueError("Unsupported draft model version")
    model["_league_id"] = league_id
    model["_base_index"] = {
        (row["context"], int(row["hero_id"])): row for row in model.get("base", [])
    }
    model["_action_index"] = {
        (row["action"], int(row["hero_id"])): row for row in model.get("action", [])
    }
    model["_relation_index"] = {row["key"]: row for row in model.get("relations", [])}
    for hero_id, positions in HERO_POSITION_FALLBACKS.items():
        if str(hero_id) in model.get("hero_names", {}) and not model.get("hero_positions", {}).get(str(hero_id)):
            model.setdefault("hero_positions", {})[str(hero_id)] = list(positions)
    apply_official_lane_eligibility(model)
    role_bits = {
        int(role_id): 1 << index
        for index, role_id in enumerate(model.get("role_ids", []))
    }
    model["_hero_role_masks"] = {
        int(hero_id): sum(
            role_bits.get(int(role_id), 0)
            for role_id in positions
        )
        for hero_id, positions in model.get("hero_positions", {}).items()
    }
    _CACHE[path] = (cache_key, model)
    return model


def load_learnable_model(league_id: str) -> dict[str, Any]:
    """Load the JSON conditional-choice artifact for a season."""
    path = learnable_model_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"No learnable draft model has been generated for {league_id}")
    modified = path.stat().st_mtime_ns
    cached = _LEARNABLE_CACHE.get(path)
    if cached and cached[0] == modified:
        return cached[1]
    with path.open(encoding="utf-8") as source:
        model = json.load(source)
    if (
        model.get("schema_version") != 2
        or model.get("model_type")
        != "team_aware_recency_weighted_hybrid_bilinear_choice"
    ):
        raise ValueError("Unsupported learnable draft model version")
    if str(model.get("target_season")) != league_id:
        raise ValueError("Learnable draft model target season does not match the requested league")

    features_path = feature_artifact_path(model)
    if not features_path.is_file():
        raise FileNotFoundError(f"Learnable feature vectors are missing: {features_path}")
    with features_path.open(encoding="utf-8") as source:
        feature_artifact = json.load(source)
    expected_feature_names = [*feature_artifact.get("feature_names", []), "feature_known"]
    if model.get("feature_names") != expected_feature_names:
        raise ValueError("Learnable draft model feature schema does not match the current vectors")
    role_fields = (
        "current_team_picks",
        "current_opponent_picks",
        "current_team_bans",
        "current_opponent_bans",
    )
    if model.get("role_fields") != list(role_fields):
        raise ValueError("Learnable draft model role schema is unsupported")

    hero_ids = [int(hero_id) for hero_id in model.get("hero_ids", [])]
    if not hero_ids:
        raise ValueError("Learnable draft model has no hero vocabulary")
    hero_to_index = {hero_id: index for index, hero_id in enumerate(hero_ids)}
    team_ids = [str(team_id) for team_id in model.get("team_ids", [])]
    if not team_ids or len(set(team_ids)) != len(team_ids):
        raise ValueError("Learnable draft model has an invalid team vocabulary")
    team_to_index = {team_id: index for index, team_id in enumerate(team_ids)}
    features_by_id = {
        int(row["hero_id"]): [*row["vector"], float(row.get("feature_known", True))]
        for row in feature_artifact.get("rows", [])
        if row.get("hero_id") is not None
    }
    feature_width = len(expected_feature_names)
    feature_matrix = [
        features_by_id.get(hero_id, [0.0] * feature_width) for hero_id in hero_ids
    ]
    parameters = model.get("parameters", {})
    required_parameters = {
        "feature_projection",
        "hero_residual",
        "context_embedding",
        "state_projection",
        "source_embedding",
        "acting_team_embedding",
        "opponent_team_embedding",
        "hero_bias",
    }
    if set(parameters) != required_parameters:
        raise ValueError("Learnable draft model parameters are incomplete")
    feature_projection = parameters["feature_projection"]
    if len(feature_projection) != feature_width or not feature_projection[0]:
        raise ValueError("Learnable draft model feature width is invalid")
    embedding_dim = len(feature_projection[0])
    if (
        len(parameters["hero_residual"]) != len(hero_ids)
        or any(len(row) != embedding_dim for row in parameters["hero_residual"])
        or len(parameters["context_embedding"]) != len(model.get("context_keys", []))
        or any(len(row) != embedding_dim for row in parameters["context_embedding"])
        or len(parameters["hero_bias"]) != len(hero_ids)
    ):
        raise ValueError("Learnable draft model hero vocabulary is invalid")
    state_width = 4 * (2 * feature_width + 1)
    if (
        len(parameters["state_projection"]) != state_width
        or any(len(row) != embedding_dim for row in parameters["state_projection"])
        or len(parameters["source_embedding"]) != 4
        or any(
            len(role_rows) != len(hero_ids)
            or any(len(row) != embedding_dim for row in role_rows)
            for role_rows in parameters["source_embedding"]
        )
        or len(parameters["acting_team_embedding"]) != len(team_ids)
        or any(
            len(row) != embedding_dim
            for row in parameters["acting_team_embedding"]
        )
        or len(parameters["opponent_team_embedding"]) != len(team_ids)
        or any(
            len(row) != embedding_dim
            for row in parameters["opponent_team_embedding"]
        )
    ):
        raise ValueError("Learnable draft model parameter dimensions are invalid")
    context_to_index = {
        (str(action), str(side), int(slot)): index
        for index, (action, side, slot) in enumerate(model.get("context_keys", []))
    }
    candidate_representations = [
        [
            sum(features[feature_index] * feature_projection[feature_index][dimension]
                for feature_index in range(feature_width))
            + parameters["hero_residual"][hero_index][dimension]
            for dimension in range(embedding_dim)
        ]
        for hero_index, features in enumerate(feature_matrix)
    ]
    model["_hero_ids"] = hero_ids
    model["_hero_to_index"] = hero_to_index
    model["_team_to_index"] = team_to_index
    model["_feature_matrix"] = feature_matrix
    model["_parameters"] = parameters
    model["_context_to_index"] = context_to_index
    model["_candidate_representations"] = candidate_representations
    _LEARNABLE_CACHE[path] = (modified, model)
    return model


def load_sequence_model(league_id: str) -> dict[str, Any]:
    """Load and prepare a sequence draft artifact for NumPy inference."""
    path = sequence_model_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"No sequence draft model has been generated for {league_id}")
    with path.open(encoding="utf-8") as source:
        model = json.load(source)
    if (
        model.get("schema_version") not in {3, 4}
        or model.get("model_type") != "frozen_bag_gru_residual_choice"
    ):
        raise ValueError("Unsupported sequence draft model version")
    if str(model.get("target_season")) != league_id:
        raise ValueError("Sequence draft model target season does not match the requested league")
    parameter_hash = hashlib.sha256(
        json.dumps(
            model.get("parameters", {}),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if parameter_hash != model.get("parameters_sha256"):
        raise ValueError("Sequence draft model parameter checksum is invalid")

    features_path = feature_artifact_path(model)
    if not features_path.is_file():
        raise FileNotFoundError(f"Sequence feature vectors are missing: {features_path}")
    calibration_path = sequence_calibration_path(league_id)
    calibration_mode = os.getenv("DRAFT_SEQUENCE_CALIBRATION", "off").strip().lower()
    signature = (
        path.stat().st_mtime_ns,
        features_path.stat().st_mtime_ns,
        calibration_path.stat().st_mtime_ns if calibration_path.is_file() else -1,
        calibration_mode,
    )
    cached = _SEQUENCE_CACHE.get(path)
    if cached and cached[0] == signature:
        return cached[1]
    with features_path.open(encoding="utf-8") as source:
        feature_artifact = json.load(source)
    expected_feature_names = [*feature_artifact.get("feature_names", []), "feature_known"]
    if model.get("feature_names") != expected_feature_names:
        raise ValueError("Sequence model feature schema does not match the current vectors")

    hero_ids = [int(hero_id) for hero_id in model.get("hero_ids", [])]
    team_ids = [str(team_id) for team_id in model.get("team_ids", [])]
    if not hero_ids or len(set(hero_ids)) != len(hero_ids):
        raise ValueError("Sequence draft model has an invalid hero vocabulary")
    if not team_ids or len(set(team_ids)) != len(team_ids):
        raise ValueError("Sequence draft model has an invalid team vocabulary")
    hero_to_index = {hero_id: index for index, hero_id in enumerate(hero_ids)}
    team_to_index = {team_id: index + 1 for index, team_id in enumerate(team_ids)}
    feature_width = len(expected_feature_names)
    features_by_id = {
        int(row["hero_id"]): [*row["vector"], float(row.get("feature_known", True))]
        for row in feature_artifact.get("rows", [])
        if row.get("hero_id") is not None
    }
    feature_matrix = np.asarray(
        [features_by_id.get(hero_id, [0.0] * feature_width) for hero_id in hero_ids],
        dtype=np.float32,
    )
    model["_hero_ids"] = hero_ids
    model["_hero_to_index"] = hero_to_index
    model["_team_to_index"] = team_to_index
    model["_prepared"] = prepare_sequence_parameters(model, feature_matrix)
    model["_model_fingerprint"] = semantic_model_fingerprint(model, feature_matrix)
    model["_calibration_mode"] = calibration_mode
    model["_calibration_path"] = calibration_path
    _SEQUENCE_CACHE[path] = (signature, model)
    return model


def learned_feature_space(league_id: str) -> dict[str, Any]:
    """Return the notebook-exported 2-D learned candidate representation."""
    path = feature_space_path(league_id)
    if not path.is_file():
        raise FileNotFoundError(f"No learned hero feature space has been generated for {league_id}")
    with path.open(encoding="utf-8") as source:
        feature_space = json.load(source)
    if (
        feature_space.get("schema_version") != 1
        or feature_space.get("projection") != "pca"
        or str(feature_space.get("target_season")) != league_id
    ):
        raise ValueError("Unsupported learned hero feature space version")
    model = load_model(league_id)
    rows = []
    for row in feature_space.get("rows", []):
        hero_id = int(row["hero_id"])
        positions = [
            int(position)
            for position in model.get("hero_positions", {}).get(str(hero_id), [])
            if int(position) in POSITION_LANES
        ]
        primary_lane = str(row.get("primary_lane") or "unknown")
        if primary_lane == "unknown" and positions:
            primary_lane = POSITION_LANES[positions[-1]]
        rows.append(
            {
                **row,
                "hero_name": model["hero_names"].get(str(hero_id), row.get("hero_name", str(hero_id))),
                "hero_icon": model["hero_icons"].get(str(hero_id), ""),
                "positions": positions,
                "primary_lane": primary_lane,
            }
        )
    return {
        **{key: value for key, value in feature_space.items() if key != "rows"},
        "rows": rows,
    }


def metadata(league_id: str) -> dict[str, Any]:
    model = load_model(league_id)
    learnable_ready = (
        learnable_model_path(league_id).is_file()
        and (
            DRAFT_FEATURES_PATH.is_file()
            or LEGACY_SPECIALTY_FEATURES_PATH.is_file()
        )
    )
    sequence_ready = (
        sequence_model_path(league_id).is_file()
        and DRAFT_FEATURES_PATH.is_file()
    )
    personalized_ready = personalized_model_path(league_id).is_file()
    return {
        "league_id": league_id,
        "generated_at": model["generated_at"],
        "training_inputs": model["training_inputs"],
        "training_decisions": model["training_decisions"],
        "effective_training_decisions": model.get("effective_training_decisions"),
        "heroes": [
            {
                "hero_id": hero_id,
                "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
                "positions": model.get("hero_positions", {}).get(str(hero_id), []),
            }
            for hero_id in model["hero_ids"]
        ],
        "draft_sequence": model["draft_sequence"],
        "default_model_type": ("personalized" if personalized_ready else "sequence" if sequence_ready else "learnable" if learnable_ready else "stats"),
        "available_models": [
            {
                "id": "stats",
                "label": "Statistical model",
                "available": True,
                "description": "Historical rates with smoothed hero relationships.",
            },
            {
                "id": "learnable",
                "label": "Team-aware learnable hybrid",
                "available": learnable_ready,
                "description": (
                    "Learned team, opponent, specialty, hero, and draft-context embeddings."
                ),
            },
            {
                "id": "sequence",
                "label": "Chronological bag + GRU",
                "available": sequence_ready,
                "description": (
                    "A frozen bag model plus a chronological GRU correction."
                ),
            },
            {
                "id": "personalized",
                "label": "Sequence + player familiarity",
                "available": personalized_ready,
                "description": "Production draft policy with a bounded player-familiarity correction and exact sequence fallback.",
            },
        ],
    }


def _relation_key(context: str, role: str, source_id: int, target_id: int) -> str:
    return f"{context}|{role}|{source_id}|{target_id}"


def _used_heroes(state: dict[str, Any]) -> set[int]:
    return {
        int(hero_id)
        for key in ("blue_picks", "red_picks", "blue_bans", "red_bans")
        for hero_id in state.get(key, [])
    }


def _role_assignment_masks(model: dict[str, Any], hero_ids: list[int]) -> set[int]:
    """Return every distinct-role assignment available to the supplied heroes."""
    assignments = {0}
    role_masks = model["_hero_role_masks"]
    for hero_id in hero_ids:
        hero_mask = role_masks.get(int(hero_id), 0)
        assignments = {
            assigned_mask | role_bit
            for assigned_mask in assignments
            for role_bit in (1 << index for index in range(hero_mask.bit_length()))
            if hero_mask & role_bit and not assigned_mask & role_bit
        }
        if not assignments:
            break
    return assignments


def _roles_are_feasible(model: dict[str, Any], hero_ids: list[int]) -> bool:
    """Check if the picked heroes have a one-to-one assignment to roles."""
    return bool(_role_assignment_masks(model, hero_ids))


def _heroes_that_fit_open_roles(
    model: dict[str, Any], hero_ids: list[int], candidates: list[int]
) -> list[int]:
    """Keep candidates that can complete a distinct-role assignment for a team."""
    assignments = _role_assignment_masks(model, hero_ids)
    role_masks = model["_hero_role_masks"]
    return [
        hero_id
        for hero_id in candidates
        if any(role_masks.get(hero_id, 0) & ~assignment for assignment in assignments)
    ]


def _legal_heroes(
    model: dict[str, Any], state: dict[str, Any], step: dict[str, Any]
) -> list[int]:
    used = _used_heroes(state)
    if state.get("legal_hero_ids") is not None:
        candidates = sorted(
            {
                int(hero_id)
                for hero_id in state["legal_hero_ids"]
                if int(hero_id) > 0 and int(hero_id) not in used
            }
        )
    else:
        candidates = [hero_id for hero_id in model["hero_ids"] if hero_id not in used]
    side = str(step["side"])
    if step["action"] == "ban":
        # A ban is only strategically relevant when the opposing side can
        # still use the hero in one of its unfilled roles.  This prevents, for
        # example, a pure mid hero from being forecast as a ban after the
        # opponent has already locked its only mid assignment.
        opponent = "red" if side == "blue" else "blue"
        opponent_picks = [
            int(hero_id) for hero_id in state.get(f"{opponent}_picks", [])
        ]
        return _heroes_that_fit_open_roles(model, opponent_picks, candidates)
    previous_match_used = {
        int(hero_id)
        for hero_id in state.get(f"{side}_used_previous_battles", [])
    }
    candidates = [hero_id for hero_id in candidates if hero_id not in previous_match_used]
    team_picks = [int(hero_id) for hero_id in state.get(f"{side}_picks", [])]
    return _heroes_that_fit_open_roles(model, team_picks, candidates)


def _restrict_probabilities_to_legal_heroes(
    model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Drop role-infeasible candidates from a scored distribution and renormalize."""
    legal = {int(hero_id) for hero_id in _legal_heroes(model, state, step)}
    kept = [dict(row) for row in rows if int(row["hero_id"]) in legal]
    total = sum(float(row["probability"]) for row in kept)
    if total <= 0:
        return []
    for row in kept:
        row["probability"] = float(row["probability"]) / total
    return kept


def _game_available_heroes(
    model: dict[str, Any], state: dict[str, Any], step: dict[str, Any]
) -> list[int]:
    """Candidate contract used to evaluate and calibrate the promoted policy."""
    used = _used_heroes(state)
    source = state.get("legal_hero_ids")
    candidates = (
        sorted({int(hero_id) for hero_id in source if int(hero_id) > 0})
        if source is not None
        else list(model["hero_ids"])
    )
    candidates = [hero_id for hero_id in candidates if hero_id not in used]
    if step["action"] == "pick":
        side = str(step["side"])
        unavailable = {int(hero_id) for hero_id in state.get(f"{side}_used_previous_battles", [])}
        candidates = [hero_id for hero_id in candidates if hero_id not in unavailable]
    return candidates


def _visible_sources(state: dict[str, Any], side: str) -> list[tuple[str, int]]:
    own = "blue" if side == "blue" else "red"
    opponent = "red" if own == "blue" else "blue"
    fields = (
        ("own_pick", state.get(f"{own}_picks", [])),
        ("opponent_pick", state.get(f"{opponent}_picks", [])),
        ("own_ban", state.get(f"{own}_bans", [])),
        ("opponent_ban", state.get(f"{opponent}_bans", [])),
    )
    return [(role, int(hero_id)) for role, hero_ids in fields for hero_id in hero_ids if int(hero_id) > 0]


def _predict_stats(model: dict[str, Any], state: dict[str, Any], step: dict[str, Any]) -> list[dict[str, Any]]:
    config = model["config"]
    action = step["action"]
    context = f"{action}|{step['side']}|{int(step['team_action_type_number'])}"
    alpha = float(config["alpha"])
    max_log_lift = math.log(float(config["max_lift"]))
    max_meta_log_lift = math.log(float(config.get("max_meta_lift", 1.0)))
    sources = _visible_sources(state, step["side"])
    scores: list[tuple[int, float]] = []

    for hero_id in _legal_heroes(model, state, step):
        action_row = model["_action_index"].get((action, hero_id))
        action_probability = (
            action_row["selections"] / action_row["opportunities"]
            if action_row and action_row["opportunities"]
            else 1e-9
        )
        base_row = model["_base_index"].get((context, hero_id))
        base_probability = (
            (base_row["selections"] + alpha * action_probability)
            / (base_row["opportunities"] + alpha)
            if base_row
            else action_probability
        )
        score = math.log(max(base_probability, 1e-12))
        if int(step["bp_order"]) <= 5:
            meta = model.get("meta", {})
            opportunities = float(meta.get("opportunities", {}).get(str(hero_id), 0))
            if opportunities:
                meta_rate = float(meta.get("selections", {}).get(str(hero_id), 0)) / opportunities
                baseline_rate = float(meta.get("baseline_rate", 0))
                if baseline_rate:
                    score += float(config.get("meta_weight", 0.0)) * max(
                        -max_meta_log_lift,
                        min(max_meta_log_lift, math.log(max(meta_rate, 1e-12) / baseline_rate)),
                    )
        for role, source_id in sources:
            relation = model["_relation_index"].get(
                _relation_key(context, role, source_id, hero_id)
            )
            if not relation:
                continue
            relation_probability = (
                relation["selections"] + alpha * base_probability
            ) / (relation["opportunities"] + alpha)
            lift = relation_probability / max(base_probability, 1e-12)
            weight = relation["opportunities"] / (
                relation["opportunities"] + float(config["shrinkage"])
            )
            role_weight = (
                float(config.get("own_pick_relation_weight", 1.0))
                if role == "own_pick"
                else 1.0
            )
            score += role_weight * weight * max(-max_log_lift, min(max_log_lift, math.log(lift)))
        scores.append((hero_id, score))

    maximum = max((score for _, score in scores), default=0.0)
    weights = [(hero_id, math.exp(score - maximum)) for hero_id, score in scores]
    total = sum(weight for _, weight in weights) or 1.0
    return [
        {
            "hero_id": hero_id,
            "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
            "probability": weight / total,
        }
        for hero_id, weight in sorted(weights, key=lambda item: item[1], reverse=True)
    ]


def _learnable_role_hero_ids(state: dict[str, Any], side: str) -> tuple[list[int], ...]:
    opponent = "red" if side == "blue" else "blue"
    return (
        [int(hero_id) for hero_id in state.get(f"{side}_picks", []) if int(hero_id) > 0],
        [int(hero_id) for hero_id in state.get(f"{opponent}_picks", []) if int(hero_id) > 0],
        [int(hero_id) for hero_id in state.get(f"{side}_bans", []) if int(hero_id) > 0],
        [int(hero_id) for hero_id in state.get(f"{opponent}_bans", []) if int(hero_id) > 0],
    )


def _predict_learnable(
    base_model: dict[str, Any],
    learnable_model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
) -> list[dict[str, Any]]:
    """Score legal heroes with the exported recency-weighted choice model."""
    parameters = learnable_model["_parameters"]
    feature_matrix = learnable_model["_feature_matrix"]
    hero_to_index = learnable_model["_hero_to_index"]
    role_hero_ids = _learnable_role_hero_ids(state, str(step["side"]))
    feature_width = len(feature_matrix[0])
    embedding_dim = len(parameters["hero_residual"][0])
    static_state: list[float] = []
    for hero_ids in role_hero_ids:
        indices = [hero_to_index[hero_id] for hero_id in hero_ids if hero_id in hero_to_index]
        vectors = [feature_matrix[index] for index in indices]
        static_state.extend(
            sum((vector[feature_index] for vector in vectors), 0.0)
            for feature_index in range(feature_width)
        )
        static_state.extend(
            max((vector[feature_index] for vector in vectors), default=0.0)
            for feature_index in range(feature_width)
        )
        static_state.append(float(len(indices)))
    context_key = (
        str(step["action"]),
        str(step["side"]),
        int(step["team_action_type_number"]),
    )
    try:
        context_index = learnable_model["_context_to_index"][context_key]
    except KeyError as exc:
        raise ValueError(f"Learnable draft model has no context for {context_key}") from exc
    query = [
        parameters["context_embedding"][context_index][dimension]
        + sum(
            static_state[state_index] * parameters["state_projection"][state_index][dimension]
            for state_index in range(len(static_state))
        )
        for dimension in range(embedding_dim)
    ]
    side = str(step["side"])
    opponent_side = "red" if side == "blue" else "blue"
    acting_team_id = str(state.get(f"{side}_team_id") or "")
    opponent_team_id = str(state.get(f"{opponent_side}_team_id") or "")
    team_to_index = learnable_model["_team_to_index"]
    acting_team_index = team_to_index.get(acting_team_id)
    opponent_team_index = team_to_index.get(opponent_team_id)
    for parameter_name, team_index in (
        ("acting_team_embedding", acting_team_index),
        ("opponent_team_embedding", opponent_team_index),
    ):
        if team_index is None:
            continue
        for dimension in range(embedding_dim):
            query[dimension] += parameters[parameter_name][team_index][dimension]
    for role_index, hero_ids in enumerate(role_hero_ids):
        for hero_id in hero_ids:
            hero_index = hero_to_index.get(hero_id)
            if hero_index is not None:
                for dimension in range(embedding_dim):
                    query[dimension] += parameters["source_embedding"][role_index][hero_index][dimension]
    logits = [
        sum(value * query[dimension] for dimension, value in enumerate(candidate))
        + parameters["hero_bias"][hero_index]
        for hero_index, candidate in enumerate(learnable_model["_candidate_representations"])
    ]
    legal_hero_ids = _legal_heroes(base_model, state, step)
    candidate_indices = [hero_to_index[hero_id] for hero_id in legal_hero_ids if hero_id in hero_to_index]
    if not candidate_indices:
        return []
    candidate_logits = [logits[index] for index in candidate_indices]
    maximum = max(candidate_logits)
    weights = [math.exp(score - maximum) for score in candidate_logits]
    total = sum(weights)
    probabilities = [weight / total for weight in weights]
    team_metadata = (
        {
            "team_context_level": "learned_embeddings",
            "team_context_decisions": int(
                learnable_model.get("team_training_decisions", {}).get(
                    acting_team_id, 0
                )
            ),
            "acting_team_known": acting_team_index is not None,
            "opponent_team_known": opponent_team_index is not None,
        }
        if acting_team_index is not None or opponent_team_index is not None
        else {}
    )
    rows = [
        {
            "hero_id": hero_id,
            "hero_name": base_model["hero_names"].get(str(hero_id), str(hero_id)),
            "probability": float(probability),
            **team_metadata,
        }
        for hero_id, probability in zip(
            (hero_id for hero_id in legal_hero_ids if hero_id in hero_to_index),
            probabilities,
        )
    ]
    return sorted(rows, key=lambda row: row["probability"], reverse=True)


def _sequence_history(
    base_model: dict[str, Any],
    sequence_model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Reconstruct the strict token order from the canonical draft prefix."""
    next_order = int(step["bp_order"])
    lists = {
        (side, action): [
            int(hero_id)
            for hero_id in state.get(
                f"{side}_{'picks' if action == 'pick' else 'bans'}", []
            )
        ]
        for side in ("blue", "red")
        for action in ("pick", "ban")
    }
    cursors = {key: 0 for key in lists}
    heroes: list[int] = []
    actions: list[int] = []
    sides: list[int] = []
    relations: list[int] = []
    positions: list[int] = []
    hero_to_index = sequence_model["_hero_to_index"]
    next_side = str(step["side"])
    for historical_step in base_model["draft_sequence"]:
        position = int(historical_step["bp_order"])
        if position >= next_order:
            break
        side = str(historical_step["side"])
        action = str(historical_step["action"])
        key = (side, action)
        cursor = cursors[key]
        if cursor >= len(lists[key]):
            raise ValueError(
                f"Draft state is missing the hero selected at bp_order={position}"
            )
        hero_id = lists[key][cursor]
        cursors[key] += 1
        try:
            hero_index = hero_to_index[hero_id]
        except KeyError as exc:
            raise ValueError(
                f"Sequence model has no history embedding for hero_id={hero_id}"
            ) from exc
        heroes.append(hero_index)
        actions.append(ACTION_INDEX[action])
        sides.append(SIDE_INDEX[side])
        is_own = side == next_side
        relations.append(
            1 if action == "pick" and is_own
            else 2 if action == "pick"
            else 3 if is_own
            else 4
        )
        positions.append(position)
    excess = [
        f"{side}_{action}"
        for (side, action), values in lists.items()
        if cursors[(side, action)] != len(values)
    ]
    if excess:
        raise ValueError(
            "Draft state contains selections after its bp_order: " + ", ".join(excess)
        )
    return tuple(
        np.asarray(values, dtype=np.int64)
        for values in (heroes, actions, sides, relations, positions)
    )


def _predict_sequence(
    base_model: dict[str, Any],
    sequence_model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
) -> list[dict[str, Any]]:
    """Score legal heroes with the NumPy bag-plus-GRU runtime."""
    (
        history_heroes,
        history_actions,
        history_sides,
        history_relations,
        history_positions,
    ) = _sequence_history(base_model, sequence_model, state, step)
    hero_ids = sequence_model["_hero_ids"]
    hero_to_index = sequence_model["_hero_to_index"]
    candidate_source = _game_available_heroes if sequence_model.get("candidate_kind") == "familiarity_residual" else _legal_heroes
    legal_hero_ids = [hero_id for hero_id in candidate_source(base_model, state, step) if hero_id in hero_to_index]
    if not legal_hero_ids:
        return []
    legal_indices = np.asarray(
        [hero_to_index[hero_id] for hero_id in legal_hero_ids], dtype=np.int64
    )
    legal_mask = np.zeros(len(hero_ids), dtype=np.bool_)
    legal_mask[legal_indices] = True
    side = str(step["side"])
    opponent_side = "red" if side == "blue" else "blue"
    team_to_index = sequence_model["_team_to_index"]
    acting_team_id = str(state.get(f"{side}_team_id") or "")
    opponent_team_id = str(state.get(f"{opponent_side}_team_id") or "")
    own_previous = {
        int(hero_id)
        for hero_id in state.get(f"{side}_used_previous_battles", [])
        if int(hero_id) in hero_to_index
    }
    opponent_previous = {
        int(hero_id)
        for hero_id in state.get(f"{opponent_side}_used_previous_battles", [])
        if int(hero_id) in hero_to_index
    }
    own_previous_mask = np.asarray(
        [hero_id in own_previous for hero_id in hero_ids], dtype=np.bool_
    )
    opponent_previous_mask = np.asarray(
        [hero_id in opponent_previous for hero_id in hero_ids], dtype=np.bool_
    )
    acting_team = team_to_index.get(acting_team_id, 0)
    opponent_team = team_to_index.get(opponent_team_id, 0)
    logits = sequence_logits(
        sequence_model["_prepared"],
        history_heroes=history_heroes,
        history_actions=history_actions,
        history_sides=history_sides,
        history_relations=history_relations,
        history_positions=history_positions,
        next_action=ACTION_INDEX[str(step["action"])],
        next_side=SIDE_INDEX[side],
        next_position=int(step["bp_order"]),
        next_team_slot=int(step["team_action_type_number"]),
        acting_team=acting_team,
        opponent_team=opponent_team,
        legal_mask=legal_mask,
        own_previous_hero_mask=own_previous_mask,
        opponent_previous_hero_mask=opponent_previous_mask,
    )
    personalization_used = False
    personalization_reason = sequence_model.get("_personalization_reason", "safe_context_unavailable")
    if "_personalized_prepared" in sequence_model and "_context_snapshot" in sequence_model:
        feature_values, personalization_reason = personalized_candidate_features(
            sequence_model["_context_snapshot"], acting_team_id, opponent_team_id, state, side
        )
        if feature_values is not None:
            prepared_personalized = sequence_model["_personalized_prepared"]
            if prepared_personalized["kind"] == "familiarity_residual":
                logits = personalized_logits(
                    prepared_personalized,
                    {
                        "legal_mask": legal_mask[None, :],
                        "candidate_features": feature_values[None, :, :],
                        "next_actions": np.asarray([ACTION_INDEX[str(step["action"])]], dtype=np.int64),
                    },
                    logits[None, :],
                )[0]
                personalization_used = True
    calibration = sequence_model.get("_calibration")
    temperature = float(calibration.temperature if calibration is not None else 1.0)
    full_probabilities = masked_softmax(logits, legal_mask, temperature)
    probabilities = full_probabilities[legal_indices]
    team_metadata = {
        "team_context_level": "sequence_embeddings",
        "team_context_decisions": int(
            sequence_model.get("team_training_decisions", {}).get(
                acting_team_id, 0
            )
        ),
        "acting_team_known": acting_team > 0,
        "opponent_team_known": opponent_team > 0,
        "calibration_temperature": temperature,
        "calibration_status": (
            calibration.status if calibration is not None else "uncalibrated"
        ),
        "custom_candidate_subset": state.get("legal_hero_ids") is not None,
        **(
            {
                "personalization_used": personalization_used,
                "personalization_reason": personalization_reason,
                "context_id": sequence_model.get("_context_snapshot", {}).get("context_id"),
            }
            if "_personalized_prepared" in sequence_model
            else {}
        ),
    }
    rows = [
        {
            "hero_id": hero_id,
            "hero_name": base_model["hero_names"].get(str(hero_id), str(hero_id)),
            "probability": float(probability),
            **team_metadata,
        }
        for hero_id, probability in zip(legal_hero_ids, probabilities, strict=True)
    ]
    return sorted(rows, key=lambda row: row["probability"], reverse=True)


def _predict(
    model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    learnable_model: dict[str, Any] | None = None,
    sequence_model: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if sequence_model is not None:
        return _predict_sequence(model, sequence_model, state, step)
    if learnable_model is not None:
        return _predict_learnable(model, learnable_model, state, step)
    return _apply_team_tendency(model, state, step, _predict_stats(model, state, step))


def _team_tendency_index(
    rows: tuple[dict[str, Any], ...],
) -> dict[tuple[str, str, str, str, int, str], dict[int, dict[str, Any]]]:
    index: dict[
        tuple[str, str, str, str, int, str],
        dict[int, dict[str, Any]],
    ] = {}
    for row in rows:
        level = str(row.get("context_level") or "")
        if level not in {"slot", "opponent_slot"}:
            continue
        key = (
            str(row.get("team_id") or ""),
            level,
            str(row.get("side") or ""),
            str(row.get("action") or ""),
            int(row.get("team_action_type_number") or 0),
            str(row.get("opponent_team_id") or ""),
        )
        index.setdefault(key, {})[int(row["hero_id"])] = row
    return index


def _load_team_tendency_index(
    league_id: str,
) -> tuple[
    dict[tuple[str, str, str, str, int, str], dict[int, dict[str, Any]]],
    str,
]:
    path = OUTPUT_ROOT / league_id / "team_action_tendencies.jsonl"
    modified = path.stat().st_mtime_ns
    cached = _TEAM_TENDENCY_CACHE.get(path)
    if cached and cached[0] == modified:
        return cached[1], str(modified)
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                rows.append(json.loads(line))
    index = _team_tendency_index(tuple(rows))
    _TEAM_TENDENCY_CACHE[path] = (modified, index)
    return index, str(modified)


def _apply_team_tendency(
    model: dict[str, Any],
    state: dict[str, Any],
    step: dict[str, Any],
    base_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Blend the league model with precomputed acting-team BP tendencies."""
    league_id = str(model.get("_league_id") or "")
    side = str(step.get("side") or "")
    team_id = str(state.get(f"{side}_team_id") or "")
    opponent_side = "red" if side == "blue" else "blue"
    opponent_id = str(state.get(f"{opponent_side}_team_id") or "")
    if not league_id or not team_id or not base_rows:
        return base_rows
    try:
        index, artifact_version = _load_team_tendency_index(league_id)
    except FileNotFoundError:
        return base_rows

    action = str(step.get("action") or "")
    slot = int(step.get("team_action_type_number") or 0)
    opponent_key = (
        team_id,
        "opponent_slot",
        side,
        action,
        slot,
        opponent_id,
    )
    slot_key = (team_id, "slot", side, action, slot, "")
    tendency_rows = index.get(opponent_key, {}) if opponent_id else {}
    context_level = "opponent_slot"
    context_support = max(
        (int(row.get("context_decision_count") or 0) for row in tendency_rows.values()),
        default=0,
    )
    if context_support < 3:
        tendency_rows = index.get(slot_key, {})
        context_level = "slot"
        context_support = max(
            (
                int(row.get("context_decision_count") or 0)
                for row in tendency_rows.values()
            ),
            default=0,
        )
    if not tendency_rows or context_support <= 0:
        return base_rows

    blend_weight = min(0.7, context_support / (context_support + 8.0))
    weighted: list[dict[str, Any]] = []
    for base in base_rows:
        hero_id = int(base["hero_id"])
        tendency = tendency_rows.get(hero_id)
        lift = float(tendency.get("smoothed_lift") or 1.0) if tendency else 1.0
        lift = min(3.0, max(0.35, lift))
        adjusted = float(base["probability"]) * (lift**blend_weight)
        weighted.append(
            {
                **base,
                "league_probability": float(base["probability"]),
                "team_adjustment_lift": lift,
                "team_context_level": context_level,
                "team_context_decisions": context_support,
                "team_context_artifact_version": artifact_version,
                "_adjusted_weight": adjusted,
            }
        )
    total = sum(float(row["_adjusted_weight"]) for row in weighted) or 1.0
    result = [
        {
            **{key: value for key, value in row.items() if key != "_adjusted_weight"},
            "probability": float(row["_adjusted_weight"]) / total,
        }
        for row in weighted
    ]
    return sorted(result, key=lambda row: row["probability"], reverse=True)


def _prediction_context(
    state: dict[str, Any],
    step: dict[str, Any],
    probabilities: list[dict[str, Any]],
) -> dict[str, Any] | None:
    side = str(step.get("side") or "")
    team_id = state.get(f"{side}_team_id")
    if not team_id or not probabilities or "team_context_level" not in probabilities[0]:
        return None
    opponent = "red" if side == "blue" else "blue"
    return {
        "acting_team_id": str(team_id),
        "acting_team_name": str(state.get(f"{side}_team_name") or team_id),
        "opponent_team_id": str(state.get(f"{opponent}_team_id") or ""),
        "opponent_team_name": str(state.get(f"{opponent}_team_name") or ""),
        "side": side,
        "context_level": probabilities[0]["team_context_level"],
        "context_decisions": probabilities[0]["team_context_decisions"],
        "acting_team_known": probabilities[0].get("acting_team_known", True),
        "opponent_team_known": probabilities[0].get("opponent_team_known", True),
    }


def _model_label(model_type: str, team_context: dict[str, Any] | None) -> str:
    labels = {
        "stats": "Statistical model",
        "learnable": "Team-aware learnable hybrid",
        "sequence": "Chronological bag + GRU",
        "personalized": "Sequence + player familiarity",
    }
    label = labels[model_type]
    return label + (
        " + team context" if team_context and model_type == "stats" else ""
    )


def _apply(state: dict[str, Any], step: dict[str, Any], hero_id: int) -> None:
    field = f"{step['side']}_{'picks' if step['action'] == 'pick' else 'bans'}"
    state.setdefault(field, []).append(hero_id)
    if state.get("legal_hero_ids") is not None:
        state["legal_hero_ids"] = [
            candidate for candidate in state["legal_hero_ids"] if int(candidate) != hero_id
        ]


def _prepare_prediction(
    league_id: str,
    state: dict[str, Any],
    model_type: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any] | None,
    dict[str, Any] | None,
    list[dict[str, Any]],
    int,
    dict[str, Any],
    list[dict[str, Any]],
]:
    """Validate one draft state and calculate its next-action distribution."""
    model = load_model(league_id)
    if model_type not in {"stats", "learnable", "sequence", "personalized"}:
        raise ValueError(f"Unsupported draft model type: {model_type}")
    learnable_model = (
        load_learnable_model(league_id) if model_type == "learnable" else None
    )
    sequence_model = (
        load_sequence_model(league_id)
        if model_type == "sequence"
        else load_personalized_model(league_id)
        if model_type == "personalized"
        else None
    )
    if sequence_model is not None and model_type == "sequence":
        policy_fingerprint = candidate_policy_fingerprint(
            LEGACY_POLICY_ID,
            role_map={str(key): int(value) for key, value in model["_hero_role_masks"].items()},
            availability_config={
                "role_ids": model.get("role_ids", []),
                "global_bp_previous_game_pick_exclusion": True,
                "ban_uses_opponent_open_roles": True,
            },
        )
        sequence_model["_candidate_policy_id"] = LEGACY_POLICY_ID
        sequence_model["_candidate_policy_fingerprint"] = policy_fingerprint
        sequence_model["_calibration"] = resolve_calibration(
            sequence_model["_calibration_path"],
            enabled_mode=sequence_model["_calibration_mode"],
            policy_model_type="sequence",
            model_fingerprint=sequence_model["_model_fingerprint"],
            candidate_policy_id=LEGACY_POLICY_ID,
            candidate_policy_fingerprint_value=policy_fingerprint,
        )
    elif sequence_model is not None:
        availability_config = {
            "role_ids": model.get("role_ids", []),
            "global_bp_previous_game_pick_exclusion": True,
            "ban_uses_opponent_open_roles": False,
        }
        policy_fingerprint = candidate_policy_fingerprint(
            GAME_AVAILABILITY_POLICY_ID,
            role_map={},
            availability_config=availability_config,
        )
        sequence_model["_candidate_policy_id"] = GAME_AVAILABILITY_POLICY_ID
        sequence_model["_candidate_policy_fingerprint"] = policy_fingerprint
        sequence_model["_calibration"] = resolve_calibration(
            sequence_model["_calibration_path"],
            enabled_mode="eligible",
            policy_model_type="personalized",
            model_fingerprint=str(sequence_model.get("model_fingerprint") or ""),
            candidate_policy_id=GAME_AVAILABILITY_POLICY_ID,
            candidate_policy_fingerprint_value=policy_fingerprint,
        )
    for side in ("blue", "red"):
        picks = [int(hero_id) for hero_id in state.get(f"{side}_picks", [])]
        if not _roles_are_feasible(model, picks):
            raise ValueError(
                f"{side.title()} picks cannot be assigned to distinct eligible roles"
            )
        overlap = set(picks) & {
            int(hero_id)
            for hero_id in state.get(f"{side}_used_previous_battles", [])
        }
        if overlap:
            raise ValueError(
                f"{side.title()} cannot pick heroes used in an earlier battle: "
                f"{sorted(overlap)}"
            )
    sequence = model["draft_sequence"]
    try:
        start_order = int(state["bp_order"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("A valid bp_order is required") from exc
    start_index = next(
        (
            index
            for index, step in enumerate(sequence)
            if int(step["bp_order"]) == start_order
        ),
        None,
    )
    if start_index is None:
        raise ValueError(f"bp_order={start_order} is not in the model sequence")
    next_step = sequence[start_index]
    next_probabilities = _restrict_probabilities_to_legal_heroes(
        model,
        state,
        next_step,
        _predict(model, state, next_step, learnable_model, sequence_model),
    )
    if not next_probabilities:
        raise ValueError("No legal heroes remain")
    return (
        model,
        learnable_model,
        sequence_model,
        sequence,
        start_index,
        next_step,
        next_probabilities,
    )


def predict_next_action(
    league_id: str,
    state: dict[str, Any],
    *,
    model_type: str = "stats",
    limit: int = 5,
) -> dict[str, Any]:
    """Return the next legal BP distribution without running future rollouts."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    (
        model,
        learnable_model,
        sequence_model,
        _,
        _,
        next_step,
        probabilities,
    ) = _prepare_prediction(
        league_id,
        state,
        model_type,
    )
    team_context = _prediction_context(state, next_step, probabilities)
    active_model = sequence_model or learnable_model or model
    return {
        "model_generated_at": active_model.get("generated_at", model["generated_at"]),
        "model_type": model_type,
        "model_label": _model_label(model_type, team_context),
        "team_context": team_context,
        "next_step": next_step,
        "candidate_count": len(probabilities),
        "next_action_probabilities": probabilities[:limit],
        **(
            {"calibration": sequence_model["_calibration"].metadata()}
            if sequence_model is not None
            else {}
        ),
    }


def sample_forced_draft_completions(
    league_id: str,
    state: dict[str, Any],
    *,
    forced_first_hero_id: int,
    rollouts: int,
    seed: int | None,
    model_type: str = "stats",
) -> dict[str, Any]:
    """Complete drafts after forcing one currently legal pick or ban.

    This is the policy half of the recommendation workflow.  The caller owns
    leaf-state evaluation so the existing prediction simulator remains
    independent from any particular winner/value model.
    """
    if rollouts < 1:
        raise ValueError("rollouts must be at least 1")
    (
        model,
        learnable_model,
        sequence_model,
        sequence,
        start_index,
        next_step,
        next_probabilities,
    ) = _prepare_prediction(league_id, state, model_type)
    probability_by_hero = {
        int(row["hero_id"]): float(row["probability"])
        for row in next_probabilities
    }
    forced = int(forced_first_hero_id)
    if forced not in probability_by_hero:
        raise ValueError(f"hero_id={forced} is not legal for the next action")

    randomizer = random.Random(seed)
    completions: list[dict[str, Any]] = []
    remaining_sequence = sequence[start_index:]
    for _ in range(rollouts):
        current = json.loads(json.dumps(state))
        path: list[dict[str, Any]] = []
        completed = True
        for index, step in enumerate(remaining_sequence):
            if index == 0:
                selected = forced
            else:
                probabilities = _predict(
                    model, current, step, learnable_model, sequence_model
                )
                if not probabilities:
                    completed = False
                    break
                selected = randomizer.choices(
                    [int(row["hero_id"]) for row in probabilities],
                    weights=[float(row["probability"]) for row in probabilities],
                    k=1,
                )[0]
            path.append(
                {
                    "bp_order": int(step["bp_order"]),
                    "side": str(step["side"]),
                    "action": str(step["action"]),
                    "hero_id": selected,
                    "hero_name": model["hero_names"].get(str(selected), str(selected)),
                }
            )
            _apply(current, step, selected)
        completions.append(
            {
                "completed": completed,
                "state": current,
                "path": path,
            }
        )
    return {
        "model_generated_at": (
            sequence_model or learnable_model or model
        ).get("generated_at", model["generated_at"]),
        "model_type": model_type,
        "next_step": next_step,
        "forced_hero_id": forced,
        "forced_policy_probability": probability_by_hero[forced],
        **(
            {"calibration": sequence_model["_calibration"].metadata()}
            if sequence_model is not None
            else {}
        ),
        "completions": completions,
    }


def simulate(
    league_id: str,
    state: dict[str, Any],
    rollouts: int,
    seed: int | None,
    *,
    model_type: str = "stats",
    max_actions: int | None = None,
    combination_side: str | None = None,
    combination_size: int = 0,
) -> dict[str, Any]:
    if max_actions is not None and max_actions < 1:
        raise ValueError("max_actions must be at least 1")
    (
        model,
        learnable_model,
        sequence_model,
        sequence,
        start_index,
        next_step,
        next_probabilities,
    ) = _prepare_prediction(league_id, state, model_type)

    randomizer = random.Random(seed)
    event_counts: dict[int, dict[int, int]] = {}
    ban_counts: dict[int, int] = {}
    combination_counts: dict[tuple[int, ...], int] = {}
    remaining_sequence = sequence[start_index:]
    if max_actions is not None:
        remaining_sequence = remaining_sequence[:max_actions]
    for _ in range(rollouts):
        current = json.loads(json.dumps(state))
        combination: list[int] = []
        for index, step in enumerate(remaining_sequence):
            probabilities = (
                next_probabilities
                if index == 0
                else _predict(
                    model, current, step, learnable_model, sequence_model
                )
            )
            if not probabilities:
                break
            hero_ids = [row["hero_id"] for row in probabilities]
            weights = [row["probability"] for row in probabilities]
            selected = randomizer.choices(hero_ids, weights=weights, k=1)[0]
            order_counts = event_counts.setdefault(int(step["bp_order"]), {})
            order_counts[selected] = order_counts.get(selected, 0) + 1
            if step["action"] == "ban":
                ban_counts[selected] = ban_counts.get(selected, 0) + 1
            elif (
                combination_side
                and step["side"] == combination_side
                and len(combination) < combination_size
            ):
                combination.append(selected)
            _apply(current, step, selected)
        if combination_size and len(combination) == combination_size:
            key = tuple(combination)
            combination_counts[key] = combination_counts.get(key, 0) + 1

    def rows(counts: dict[int, int], limit: int) -> list[dict[str, Any]]:
        return [
            {
                "hero_id": hero_id,
                "hero_name": model["hero_names"].get(str(hero_id), str(hero_id)),
                "probability": count / rollouts,
            }
            for hero_id, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]
        ]

    team_context = _prediction_context(state, next_step, next_probabilities)
    active_model = sequence_model or learnable_model or model
    pick_combinations = [
        {
            "hero_ids": list(hero_ids),
            "hero_names": [
                model["hero_names"].get(str(hero_id), str(hero_id))
                for hero_id in hero_ids
            ],
            "count": count,
            "probability": count / rollouts,
        }
        for hero_ids, count in sorted(
            combination_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )[:8]
    ]
    return {
        "model_generated_at": active_model.get("generated_at", model["generated_at"]),
        "model_type": model_type,
        "model_label": _model_label(model_type, team_context),
        "team_context": team_context,
        "next_step": next_step,
        "next_action_probabilities": next_probabilities,
        **(
            {"calibration": sequence_model["_calibration"].metadata()}
            if sequence_model is not None
            else {}
        ),
        "simulation": {
            "rollouts": rollouts,
            "actions_simulated": len(remaining_sequence),
            "next_actions": {str(order): rows(counts, 8) for order, counts in event_counts.items()},
            "banned_by_end": rows(ban_counts, 20),
            "pick_combinations": pick_combinations,
        },
    }
