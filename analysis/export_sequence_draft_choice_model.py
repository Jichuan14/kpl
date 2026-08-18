"""Export a PyTorch bag-plus-GRU checkpoint for NumPy-only web inference."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


MODEL_TYPE = "frozen_bag_gru_residual_choice"
SCHEMA_VERSION = 4
FEATURE_ARTIFACT_NAME = "hero_draft_feature_vectors.json"


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "analysis" / "hero_draft_feature_vectors.json").is_file():
            return candidate
    raise FileNotFoundError("Could not find the repository root")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--experiment-results", type=Path)
    parser.add_argument("--feature-artifact", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _json_tensor(tensor: Any) -> Any:
    value = tensor.detach().cpu()
    return float(value.item()) if value.ndim == 0 else value.tolist()


def _branch_parameters(state: dict[str, Any], prefix: str) -> dict[str, Any]:
    ignored = {"hero_features", "lag_embedding.weight"}
    result = {}
    dotted_prefix = f"{prefix}."
    for name, tensor in state.items():
        if not name.startswith(dotted_prefix):
            continue
        short_name = name[len(dotted_prefix) :]
        if short_name not in ignored:
            result[short_name] = _json_tensor(tensor)
    return result


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as temporary:
        json.dump(value, temporary, ensure_ascii=False, separators=(",", ":"))
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.replace(path)


def _team_training_decisions(
    repo_root: Path, league_id: str, experiment: dict[str, Any]
) -> dict[str, int]:
    release_refit = experiment.get("release_refit", {})
    excluded_match_ids = (
        set()
        if release_refit.get("trained_on_all_available_matches")
        else {
            *experiment.get("validation_match_ids", []),
            *experiment.get("holdout_match_ids", []),
            *experiment.get("excluded_future_match_ids", []),
        }
    )
    counts: Counter[str] = Counter()
    for season in experiment.get("training_seasons", []):
        path = repo_root / "analysis" / "exports" / str(season) / "bp_decisions.jsonl"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                if (
                    row.get("is_peak_battle")
                    or not row.get("acting_team_id")
                    or (
                        str(season) == league_id
                        and str(row.get("match_id")) in excluded_match_ids
                    )
                ):
                    continue
                counts[str(row["acting_team_id"])] += 1
    return dict(counts)


def main() -> None:
    args = parse_args()
    if not args.league_id or not all(
        character.isalnum() or character in "-_" for character in args.league_id
    ):
        raise ValueError("Invalid league id")

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "Export must run in the PyTorch training environment"
        ) from exc

    repo_root = find_repo_root(Path(__file__).resolve())
    checkpoint_path = args.checkpoint.resolve()
    feature_path = (
        args.feature_artifact or repo_root / "analysis" / FEATURE_ARTIFACT_NAME
    ).resolve()
    output_path = (
        args.output
        or repo_root
        / "analysis"
        / "outputs"
        / args.league_id
        / "sequence_draft_choice_model.json"
    ).resolve()

    checkpoint = torch.load(
        checkpoint_path, map_location="cpu", weights_only=False
    )
    if checkpoint.get("model_type") != "hybrid_bag_gru":
        raise ValueError("Checkpoint is not a hybrid_bag_gru model")
    config = dict(checkpoint["config"])
    hero_ids = [int(hero_id) for hero_id in checkpoint["hero_ids"]]
    team_ids = [str(team_id) for team_id in checkpoint["team_ids"]]
    state = checkpoint["state_dict"]

    feature_artifact = json.loads(feature_path.read_text(encoding="utf-8"))
    feature_names = [*feature_artifact.get("feature_names", []), "feature_known"]
    if checkpoint["feature_names"] != feature_names:
        raise ValueError("Checkpoint and production feature schemas differ")
    features_by_id = {
        int(row["hero_id"]): [
            *row["vector"],
            float(row.get("feature_known", True)),
        ]
        for row in feature_artifact.get("rows", [])
        if row.get("hero_id") is not None
    }
    exported_features = torch.tensor(
        [features_by_id.get(hero_id, [0.0] * len(feature_names)) for hero_id in hero_ids],
        dtype=torch.float32,
    )
    if not torch.allclose(state["bag.hero_features"], exported_features):
        raise ValueError("Checkpoint hero features differ from the production artifact")
    if not torch.equal(state["bag.hero_features"], state["gru.hero_features"]):
        raise ValueError("Bag and GRU branches use different hero features")

    experiment: dict[str, Any] = {}
    model_result: dict[str, Any] = {}
    if args.experiment_results:
        experiment = json.loads(
            args.experiment_results.resolve().read_text(encoding="utf-8")
        )
        model_result = experiment.get("models", {}).get("hybrid_bag_gru", {})
    release_refit = experiment.get("release_refit", {})
    trained_on_all_available_matches = bool(
        release_refit.get("trained_on_all_available_matches")
    )

    checkpoint_hash = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    parameters = {
        "residual_scale_logit": _json_tensor(state["residual_scale_logit"]),
        "bag": _branch_parameters(state, "bag"),
        "gru": _branch_parameters(state, "gru"),
    }
    parameters_sha256 = hashlib.sha256(
        json.dumps(
            parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "model_type": MODEL_TYPE,
        "target_season": args.league_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "feature_artifact": feature_path.name,
        "feature_names": feature_names,
        "hero_ids": hero_ids,
        "hero_names": {
            str(hero_id): str(checkpoint.get("hero_names", {}).get(hero_id, hero_id))
            for hero_id in hero_ids
        },
        "team_ids": team_ids,
        "team_training_decisions": _team_training_decisions(
            repo_root, args.league_id, experiment
        ),
        "config": config,
        "training": {
            "training_seasons": experiment.get("training_seasons", []),
            "season_weights": experiment.get("season_weights", {}),
            "training_decisions": int(
                release_refit.get("train_decisions", experiment.get("train_decisions", 0))
            ),
            "validation_match_ids": experiment.get("validation_match_ids", []),
            "holdout_match_ids": experiment.get("holdout_match_ids", []),
            "best_epoch": model_result.get("best_epoch"),
            "validation_metrics": model_result.get("validation_metrics"),
            "holdout_metrics": checkpoint.get("holdout_metrics"),
            "source_checkpoint_sha256": checkpoint_hash,
            "artifact_status": (
                "release_refit_on_all_available_matches"
                if trained_on_all_available_matches
                else "experimental_chronological_checkpoint"
            ),
            "release_refit": release_refit or None,
        },
        "parameters_sha256": parameters_sha256,
        "parameters": parameters,
    }
    _atomic_json(output_path, artifact)
    print(f"Wrote {output_path}")
    print(f"Artifact size: {output_path.stat().st_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
