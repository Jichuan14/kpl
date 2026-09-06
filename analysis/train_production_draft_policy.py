#!/usr/bin/env python3
"""Build and atomically promote the sequence + familiarity web policy."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from calibration import load_prediction_records, score_metrics


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "analysis"


def run(command: list[str]) -> None:
    print("Running:", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def source_seasons(target: str, previous: int) -> list[str]:
    available = sorted(path.parent.name for path in (ANALYSIS / "exports").glob("*/bp_decisions.jsonl"))
    if target not in available:
        raise ValueError(f"No decision export for {target}")
    index = available.index(target)
    selected = available[max(0, index - previous):index + 1]
    if len(selected) != previous + 1:
        raise ValueError("Not enough prior seasons for the production window")
    return selected


def context_cutoff(seasons: list[str]) -> str:
    latest: date | None = None
    for season in seasons:
        with (ANALYSIS / "exports" / season / "matches.jsonl").open(encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                event_date = date.fromisoformat(str(json.loads(line)["start_time"])[:10])
                latest = event_date if latest is None or event_date > latest else latest
    if latest is None:
        raise ValueError("No historical matches are available for the context snapshot")
    # Scheduled rows can already exist in the local export. Never let them
    # advance the serving snapshot beyond the day the policy is built.
    return min(latest + timedelta(days=1), date.today()).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--previous-seasons", type=int, default=4)
    parser.add_argument("--validation-series", type=int, default=10)
    parser.add_argument("--calibration-series", type=int, default=10)
    parser.add_argument("--holdout-series", type=int, default=10)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if not args.league_id or not all(c.isalnum() or c in "-_" for c in args.league_id):
        raise ValueError("Invalid league id")

    seasons = source_seasons(args.league_id, args.previous_seasons)
    output = ANALYSIS / "outputs" / args.league_id
    work = output / "sequence_familiarity_training"
    work.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    manifest = work / "split_manifest.json"
    base_artifact = work / "sequence_base.json"
    candidate_artifact = work / "sequence_familiarity_candidate.json"
    calibration = work / "personalized_draft_probability_calibration.json"
    context = work / "player_draft_context.json"

    run([python, str(ANALYSIS / "build_draft_split_manifest.py"), "--target-season", args.league_id,
         "--source-seasons", ",".join(seasons), "--validation-series", str(args.validation_series),
         "--calibration-series", str(args.calibration_series), "--holdout-series", str(args.holdout_series),
         "--output", str(manifest)])
    run([python, str(ANALYSIS / "sequence_training" / "train.py"), "--target-season", args.league_id,
         "--previous-seasons", str(args.previous_seasons), "--validation-matches", str(args.validation_series),
         "--holdout-matches", str(args.calibration_series + args.holdout_series), "--epochs", str(args.epochs),
         "--seed", str(args.seed), "--threads", str(args.threads), "--winning-pick-weight", "1.0",
         "--use-series-context", "--models", "bag_ablation,hybrid_bag_gru", "--output-dir", str(work)])
    run([python, str(ANALYSIS / "export_sequence_draft_choice_model.py"), "--league-id", args.league_id,
         "--checkpoint", str(work / "hybrid_bag_gru.pt"), "--experiment-results", str(work / "results.json"),
         "--output", str(base_artifact)])
    base_eval = work / "base_evaluation"
    run([python, str(ANALYSIS / "evaluate_draft_policy.py"), "--checkpoint", str(work / "hybrid_bag_gru.pt"),
         "--artifact", str(base_artifact), "--split-manifest", str(manifest), "--candidate-policy",
         "game_availability_v1", "--output-dir", str(base_eval)])
    run([python, str(ANALYSIS / "train_personalized_draft_choice_model.py"), "--checkpoint",
         str(work / "hybrid_bag_gru.pt"), "--split-manifest", str(manifest), "--seed", str(args.seed),
         "--epochs", str(args.epochs), "--threads", str(args.threads), "--output-dir", str(work)])
    run([python, str(ANALYSIS / "export_personalized_draft_choice_model.py"), "--checkpoint",
         str(work / f"familiarity_seed{args.seed}.pt"), "--base-artifact", str(base_artifact),
         "--output", str(candidate_artifact)])
    prediction_manifest = work / "prediction_manifest.json"
    run([python, str(ANALYSIS / "build_personalized_prediction_manifest.py"), "--artifact",
         str(candidate_artifact), "--base-prediction-manifest", str(base_eval / "prediction_manifest.json"),
         "--output", str(prediction_manifest)])
    run([python, str(ANALYSIS / "fit_draft_calibration.py"), "--predictions",
         str(work / "calibration_predictions.npz"), "--manifest", str(prediction_manifest),
         "--output", str(calibration)])

    base_holdout = score_metrics(load_prediction_records(base_eval / "holdout_predictions.npz"))
    candidate_holdout = score_metrics(load_prediction_records(work / "holdout_predictions.npz"))
    if candidate_holdout["negative_log_likelihood"] >= base_holdout["negative_log_likelihood"]:
        raise RuntimeError("Familiarity candidate failed the holdout NLL promotion gate")
    if candidate_holdout["top_5_accuracy"] < base_holdout["top_5_accuracy"]:
        raise RuntimeError("Familiarity candidate failed the holdout top-5 promotion gate")
    sidecar = json.loads(calibration.read_text(encoding="utf-8"))
    if sidecar.get("status") != "experimental" or sidecar.get("coverage", {}).get("target_excluded"):
        raise RuntimeError("Familiarity calibration is not eligible for production")
    sidecar["status"] = "eligible"
    sidecar["promotion_gate"] = {"base_holdout": base_holdout, "candidate_holdout": candidate_holdout}
    atomic_json(calibration, sidecar)
    artifact = json.loads(candidate_artifact.read_text(encoding="utf-8"))
    artifact["status"] = "production"
    artifact["calibration"] = {"status": "eligible", "temperature": sidecar["temperature"], "sidecar": "personalized_draft_probability_calibration.json"}
    atomic_json(candidate_artifact, artifact)
    context_as_of = context_cutoff(seasons)
    run([python, str(ANALYSIS / "export_player_draft_context.py"), "--source-seasons", ",".join(seasons),
         "--hero-ids", str(candidate_artifact), "--context-as-of", context_as_of, "--output", str(context)])

    validation = {"schema_version": 1, "model": "sequence_familiarity_residual_choice",
                  "candidate_policy": "game_availability_v1", "source_seasons": seasons,
                  "context_as_of": context_as_of,
                  "split_manifest": str(manifest.relative_to(ROOT)), "base_holdout": base_holdout,
                  "candidate_holdout": candidate_holdout, "temperature": sidecar["temperature"]}
    atomic_json(work / "production_validation.json", validation)
    for staged, destination in (
        (candidate_artifact, output / "personalized_draft_choice_model.json"),
        (calibration, output / "personalized_draft_probability_calibration.json"),
        (context, output / "player_draft_context.json"),
        (work / "production_validation.json", output / "draft_policy_validation.json"),
    ):
        staged.replace(destination)
    print(json.dumps(validation, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
