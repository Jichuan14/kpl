#!/usr/bin/env python3
"""Replay an evaluation checkpoint on pinned calibration and holdout series."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

ANALYSIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = ANALYSIS_DIR.parent
sys.path.insert(0, str(ANALYSIS_DIR / "sequence_training"))
sys.path.insert(0, str(REPO_ROOT / "backend"))

from calibration import PredictionRecords, score_metrics  # noqa: E402
from models import ACTION_INDEX, evaluate, load_checkpoint, prepare_data  # noqa: E402
from splits import canonical_sha256, validate_split_manifest  # noqa: E402
from app.services.draft_calibration import (  # noqa: E402
    GAME_AVAILABILITY_POLICY_ID,
    LEGACY_POLICY_ID,
    candidate_policy_fingerprint,
    semantic_model_fingerprint,
)
from app.services.draft_simulator import _legal_heroes, load_model  # noqa: E402


def _checkpoint_fingerprint(payload: dict[str, Any]) -> str:
    state = {
        key: hashlib.sha256(value.detach().cpu().numpy().tobytes()).hexdigest()
        for key, value in sorted(payload["state_dict"].items())
    }
    return canonical_sha256({
        "model_type": payload["model_type"], "config": payload["config"],
        "hero_ids": payload["hero_ids"], "team_ids": payload["team_ids"],
        "feature_names": payload["feature_names"], "state_sha256": state,
    })


def _phase(position: int) -> str:
    if position <= 5:
        return "opening_bans_and_first_pick"
    if position <= 10:
        return "first_pick_phase"
    if position <= 16:
        return "second_ban_phase"
    return "closing_picks"


def _state(row: dict[str, Any]) -> dict[str, Any]:
    side = str(row["side"])
    opponent = "red" if side == "blue" else "blue"
    value: dict[str, Any] = {"bp_order": int(row["bp_order"]), "legal_hero_ids": row["legal_hero_ids"]}
    value[f"{side}_picks"] = row.get("current_team_picks", [])
    value[f"{opponent}_picks"] = row.get("current_opponent_picks", [])
    value[f"{side}_bans"] = row.get("current_team_bans", [])
    value[f"{opponent}_bans"] = row.get("current_opponent_bans", [])
    value[f"{side}_used_previous_battles"] = row.get("team_used_in_previous_battles", [])
    value[f"{opponent}_used_previous_battles"] = row.get("opponent_used_in_previous_battles", [])
    return value


def _raw_rows(seasons: list[str]) -> dict[tuple[str, str, int], dict[str, Any]]:
    result = {}
    for season in seasons:
        path = ANALYSIS_DIR / "exports" / season / "bp_decisions.jsonl"
        with path.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    row = json.loads(line)
                    result[(str(row["match_id"]), str(row["battle_id"]), int(row["bp_order"]))] = row
    return result


def _subset(dataset: Any, indices: np.ndarray, logits: np.ndarray, masks: np.ndarray) -> PredictionRecords:
    action_names = np.where(dataset.next_actions.numpy()[indices] == ACTION_INDEX["pick"], "pick", "ban")
    positions = dataset.next_positions.numpy()[indices]
    return PredictionRecords(
        logits=logits[indices].astype(np.float64), accepted_mask=masks[indices],
        targets=dataset.targets.numpy()[indices],
        series_ids=np.asarray(dataset.match_ids)[indices].astype(str),
        actions=action_names.astype(str),
        phases=np.asarray([_phase(int(value)) for value in positions]),
    )


def _save(path: Path, records: PredictionRecords) -> None:
    np.savez_compressed(path, logits=records.logits.astype(np.float32), accepted_mask=records.accepted_mask,
        targets=records.targets, series_ids=records.series_ids, actions=records.actions, phases=records.phases)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, help="Optional exported JSON for exact serving fingerprint")
    parser.add_argument("--split-manifest", type=Path, required=True)
    parser.add_argument("--candidate-policy", choices=[LEGACY_POLICY_ID, GAME_AVAILABILITY_POLICY_ID], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args()

    import torch
    manifest = json.loads(args.split_manifest.read_text(encoding="utf-8"))
    validate_split_manifest(manifest)
    target = str(manifest["target_season"])
    source_seasons = [str(value) for value in manifest["source_seasons"]]
    data = prepare_data(REPO_ROOT, target_season=target, previous_seasons=len(source_seasons)-1,
        validation_matches=len(manifest["splits"]["validation"]),
        holdout_matches=len(manifest["splits"]["calibration"])+len(manifest["splits"]["holdout"]),
        holdout_offset_matches=int(manifest.get("holdout_offset_series", 0)), recency_decay=0.65, winning_pick_weight=1.5)
    payload = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    if payload["hero_ids"] != data.hero_ids or payload["team_ids"] != data.team_ids:
        raise ValueError("Checkpoint vocabularies do not match the split's training-only vocabularies")
    model = load_checkpoint(args.checkpoint, torch.device("cpu"))
    all_logits: list[np.ndarray] = []
    model.eval()
    with torch.inference_mode():
        for start in range(0, len(data.holdout), args.batch_size):
            index = torch.arange(start, min(start + args.batch_size, len(data.holdout)))
            batch = data.holdout.batch(index, torch.device("cpu"))
            all_logits.append(model(batch).numpy())
    logits = np.concatenate(all_logits)
    masks = data.holdout.legal_mask.numpy().copy()
    base = load_model(target)
    role_map = {str(key): int(value) for key, value in base["_hero_role_masks"].items()}
    policy_config = {"role_ids": base.get("role_ids", []), "global_bp_previous_game_pick_exclusion": True,
                     "ban_uses_opponent_open_roles": args.candidate_policy == LEGACY_POLICY_ID}
    policy_fingerprint = candidate_policy_fingerprint(args.candidate_policy,
        role_map=role_map if args.candidate_policy == LEGACY_POLICY_ID else {}, availability_config=policy_config)
    if args.candidate_policy == LEGACY_POLICY_ID:
        rows = _raw_rows(source_seasons)
        hero_to_index = {hero_id: index for index, hero_id in enumerate(data.hero_ids)}
        masks[:] = False
        for index, (match_id, battle_id, position) in enumerate(zip(data.holdout.match_ids, data.holdout.battle_ids, data.holdout.next_positions.tolist(), strict=True)):
            row = rows[(match_id, battle_id, int(position))]
            step = {"bp_order": int(position), "side": row["side"], "action": row["action"]}
            for hero_id in _legal_heroes(base, _state(row), step):
                if hero_id in hero_to_index:
                    masks[index, hero_to_index[hero_id]] = True

    c_ids = {str(row["match_id"]) for row in manifest["splits"]["calibration"]}
    h_ids = {str(row["match_id"]) for row in manifest["splits"]["holdout"]}
    matches = np.asarray(data.holdout.match_ids)
    c_index, h_index = np.flatnonzero(np.isin(matches, list(c_ids))), np.flatnonzero(np.isin(matches, list(h_ids)))
    if len(c_index) + len(h_index) != len(data.holdout):
        raise ValueError("Prepared holdout series disagree with split manifest")
    calibration, holdout = _subset(data.holdout, c_index, logits, masks), _subset(data.holdout, h_index, logits, masks)
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    _save(output / "calibration_predictions.npz", calibration); _save(output / "holdout_predictions.npz", holdout)

    model_fingerprint = _checkpoint_fingerprint(payload)
    if args.artifact:
        artifact = json.loads(args.artifact.read_text(encoding="utf-8"))
        feature = json.loads((ANALYSIS_DIR / artifact["feature_artifact"]).read_text(encoding="utf-8"))
        width = len(artifact["feature_names"]); by_id = {int(row["hero_id"]): [*row["vector"], float(row.get("feature_known", True))] for row in feature["rows"]}
        matrix = np.asarray([by_id.get(int(hero), [0.0]*width) for hero in artifact["hero_ids"]], dtype=np.float32)
        model_fingerprint = semantic_model_fingerprint(artifact, matrix)
    training_ids = sorted(str(row["match_id"]) for row in manifest["splits"]["train"])
    report = {
        "schema_version": 1, "policy_model_type": "sequence", "model_fingerprint": model_fingerprint,
        "candidate_policy_id": args.candidate_policy, "candidate_policy_fingerprint": policy_fingerprint,
        "candidate_policy_provenance": ("current_full_season_role_map_operational_diagnostic_not_chronological" if args.candidate_policy == LEGACY_POLICY_ID else "decision_export_game_availability"),
        "context_contract_version": "bp_context_v1", "split_manifest_sha256": manifest["manifest_sha256"],
        "model_training_match_ids_sha256": canonical_sha256(training_ids),
        "checkpoint": str(args.checkpoint.resolve()), "artifact": str(args.artifact.resolve()) if args.artifact else None,
        "checkpoint_baseline_metrics": evaluate(model, data.holdout, batch_size=args.batch_size, device=torch.device("cpu")),
        "calibration_t1": score_metrics(calibration), "holdout_t1": score_metrics(holdout),
    }
    (output / "prediction_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
