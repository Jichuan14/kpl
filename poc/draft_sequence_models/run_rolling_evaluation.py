#!/usr/bin/env python3
"""Run leakage-free rolling chronological folds across matched random seeds."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

import torch

from sequence_models import (
    AppCurrentChoiceModel,
    BagAblationModel,
    HybridAppLagGRUModel,
    HybridBagGRUModel,
    MODEL_TYPES,
    ModelConfig,
    benchmark_single_prediction,
    find_repo_root,
    prepare_data,
    seed_everything,
    train_model,
)


POC_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-season", default="20260003")
    parser.add_argument("--previous-seasons", type=int, default=4)
    parser.add_argument("--validation-matches", type=int, default=10)
    parser.add_argument("--holdout-matches", type=int, default=10)
    parser.add_argument("--folds", type=int, default=3)
    parser.add_argument("--fold-stride-matches", type=int, default=10)
    parser.add_argument("--seeds", default="7,17,29")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--hidden-dim", type=int, default=48)
    parser.add_argument("--learning-rate", type=float, default=0.003)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--recency-decay", type=float, default=0.65)
    parser.add_argument("--winning-pick-weight", type=float, default=1.5)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument(
        "--models",
        default=(
            "app_current,bag_ablation,gru,pairwise,hybrid_bag_gru,"
            "hybrid_app_lag_gru"
        ),
        help=f"Comma-separated subset of: {','.join(MODEL_TYPES)}",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=POC_DIR / "artifacts" / "rolling_results.json",
    )
    return parser.parse_args()


def summary(values: list[float]) -> dict[str, float | int]:
    return {
        "runs": len(values),
        "mean": statistics.fmean(values),
        "sample_standard_deviation": statistics.stdev(values) if len(values) > 1 else 0.0,
        "minimum": min(values),
        "maximum": max(values),
    }


def aggregate_results(
    runs: list[dict[str, Any]], model_names: list[str]
) -> dict[str, Any]:
    aggregate: dict[str, Any] = {}
    metric_names = (
        "negative_log_likelihood",
        "top_1_accuracy",
        "top_5_accuracy",
    )
    for model_name in model_names:
        model_runs = [run["models"][model_name] for run in runs]
        model_aggregate: dict[str, Any] = {
            metric: summary(
                [float(run["holdout_metrics"][metric]) for run in model_runs]
            )
            for metric in metric_names
        }
        model_aggregate["best_epoch"] = summary(
            [float(run["best_epoch"]) for run in model_runs]
        )
        model_aggregate["single_prediction_milliseconds"] = summary(
            [
                float(run["single_prediction_benchmark"]["mean_milliseconds"])
                for run in model_runs
            ]
        )
        lag_aggregate = {}
        for lag in range(1, 6):
            available = [
                run["holdout_metrics"]["after_opponent_pick_by_lag"].get(str(lag))
                for run in model_runs
            ]
            available = [row for row in available if row]
            if available:
                lag_aggregate[str(lag)] = {
                    "mean_decisions_per_run": statistics.fmean(
                        float(row["decisions"]) for row in available
                    ),
                    **{
                        metric: summary([float(row[metric]) for row in available])
                        for metric in metric_names
                    },
                }
        model_aggregate["after_opponent_pick_by_lag"] = lag_aggregate
        aggregate[model_name] = model_aggregate

    def add_paired_comparisons(baseline_name: str, output_key: str) -> None:
        paired = {}
        for model_name in model_names:
            if model_name == baseline_name:
                continue
            nll_deltas = []
            top1_deltas = []
            top5_deltas = []
            lag2_nll_deltas = []
            lag2_top1_deltas = []
            lag2_top5_deltas = []
            for run in runs:
                baseline = run["models"][baseline_name]["holdout_metrics"]
                challenger = run["models"][model_name]["holdout_metrics"]
                nll_deltas.append(
                    float(challenger["negative_log_likelihood"])
                    - float(baseline["negative_log_likelihood"])
                )
                top1_deltas.append(
                    float(challenger["top_1_accuracy"])
                    - float(baseline["top_1_accuracy"])
                )
                top5_deltas.append(
                    float(challenger["top_5_accuracy"])
                    - float(baseline["top_5_accuracy"])
                )
                baseline_lag2 = baseline["after_opponent_pick_by_lag"].get("2")
                challenger_lag2 = challenger["after_opponent_pick_by_lag"].get("2")
                if baseline_lag2 and challenger_lag2:
                    lag2_nll_deltas.append(
                        float(challenger_lag2["negative_log_likelihood"])
                        - float(baseline_lag2["negative_log_likelihood"])
                    )
                    lag2_top1_deltas.append(
                        float(challenger_lag2["top_1_accuracy"])
                        - float(baseline_lag2["top_1_accuracy"])
                    )
                    lag2_top5_deltas.append(
                        float(challenger_lag2["top_5_accuracy"])
                        - float(baseline_lag2["top_5_accuracy"])
                    )
            paired[model_name] = {
                f"nll_delta_vs_{baseline_name}": summary(nll_deltas),
                f"top_1_delta_vs_{baseline_name}": summary(top1_deltas),
                f"top_5_delta_vs_{baseline_name}": summary(top5_deltas),
                f"lag_2_nll_delta_vs_{baseline_name}": summary(lag2_nll_deltas),
                f"lag_2_top_1_delta_vs_{baseline_name}": summary(lag2_top1_deltas),
                f"lag_2_top_5_delta_vs_{baseline_name}": summary(lag2_top5_deltas),
                "nll_wins": sum(delta < 0 for delta in nll_deltas),
                "top_1_wins": sum(delta > 0 for delta in top1_deltas),
                "top_5_wins": sum(delta > 0 for delta in top5_deltas),
                "lag_2_nll_wins": sum(delta < 0 for delta in lag2_nll_deltas),
                "lag_2_top_1_wins": sum(delta > 0 for delta in lag2_top1_deltas),
                "lag_2_top_5_wins": sum(delta > 0 for delta in lag2_top5_deltas),
            }
        aggregate[output_key] = paired

    if "bag_ablation" in model_names:
        add_paired_comparisons("bag_ablation", "paired_comparisons")
    if "app_current" in model_names:
        add_paired_comparisons(
            "app_current", "paired_comparisons_vs_app_current"
        )
    return aggregate


def main() -> None:
    args = parse_args()
    model_names = [name.strip() for name in args.models.split(",") if name.strip()]
    seeds = [int(seed.strip()) for seed in args.seeds.split(",") if seed.strip()]
    unknown = sorted(set(model_names) - set(MODEL_TYPES))
    if unknown:
        raise ValueError(f"Unknown models: {unknown}")
    if args.folds < 1 or not seeds:
        raise ValueError("At least one fold and seed are required")
    if "hybrid_bag_gru" in model_names and "bag_ablation" not in model_names:
        raise ValueError("hybrid_bag_gru requires bag_ablation in the same run")
    if "hybrid_bag_gru" in model_names:
        model_names.remove("bag_ablation")
        model_names.insert(0, "bag_ablation")
    if "hybrid_app_lag_gru" in model_names and "app_current" not in model_names:
        raise ValueError("hybrid_app_lag_gru requires app_current in the same run")
    if "hybrid_app_lag_gru" in model_names:
        model_names.remove("app_current")
        model_names.insert(0, "app_current")

    torch.set_num_threads(args.threads)
    device = torch.device("cpu")
    repo_root = find_repo_root(POC_DIR)
    runs: list[dict[str, Any]] = []
    print("PyTorch:", torch.__version__, flush=True)
    print(
        f"Running {args.folds} folds x {len(seeds)} seeds x "
        f"{len(model_names)} models",
        flush=True,
    )

    for fold in range(args.folds):
        offset = fold * args.fold_stride_matches
        data = prepare_data(
            repo_root,
            target_season=args.target_season,
            previous_seasons=args.previous_seasons,
            validation_matches=args.validation_matches,
            holdout_matches=args.holdout_matches,
            holdout_offset_matches=offset,
            recency_decay=args.recency_decay,
            winning_pick_weight=args.winning_pick_weight,
        )
        config = ModelConfig(
            hero_count=len(data.hero_ids),
            team_count=len(data.team_ids),
            feature_width=int(data.hero_features.shape[1]),
            hidden_dim=args.hidden_dim,
        )
        print(
            f"Fold {fold + 1}: train {len(data.train):,}, validation "
            f"{len(data.validation):,}, holdout {len(data.holdout):,}, "
            f"future excluded {len(data.excluded_future_match_ids)} matches",
            flush=True,
        )
        for seed in seeds:
            selected_bag: BagAblationModel | None = None
            selected_app: AppCurrentChoiceModel | None = None
            run: dict[str, Any] = {
                "fold": fold + 1,
                "holdout_offset_matches": offset,
                "seed": seed,
                "train_decisions": len(data.train),
                "validation_decisions": len(data.validation),
                "holdout_decisions": len(data.holdout),
                "validation_match_ids": data.validation_match_ids,
                "holdout_match_ids": data.holdout_match_ids,
                "excluded_future_match_ids": data.excluded_future_match_ids,
                "models": {},
            }
            for model_name in model_names:
                seed_everything(seed)
                model = MODEL_TYPES[model_name](config, data.hero_features)
                if isinstance(model, HybridBagGRUModel):
                    if selected_bag is None:
                        raise AssertionError(
                            "The hybrid model has no selected bag checkpoint"
                        )
                    model.initialize_from_bag(selected_bag)
                if isinstance(model, HybridAppLagGRUModel):
                    if selected_app is None:
                        raise AssertionError(
                            "The hybrid model has no selected app checkpoint"
                        )
                    model.initialize_from_app(selected_app)
                parameter_count = sum(
                    parameter.numel() for parameter in model.parameters()
                )
                (
                    model,
                    training_history,
                    validation_metrics,
                    holdout_metrics,
                ) = train_model(
                    model,
                    data.train,
                    data.validation,
                    data.holdout,
                    epochs=args.epochs,
                    batch_size=getattr(
                        model, "recommended_batch_size", args.batch_size
                    ),
                    learning_rate=getattr(
                        model, "recommended_learning_rate", args.learning_rate
                    ),
                    weight_decay=args.weight_decay,
                    device=device,
                    seed=seed,
                )
                benchmark = benchmark_single_prediction(
                    model, data.holdout, device=device, iterations=100
                )
                best_epoch = min(
                    training_history,
                    key=lambda row: row["validation_negative_log_likelihood"],
                )["epoch"]
                run["models"][model_name] = {
                    "parameters": parameter_count,
                    "trainable_parameters": sum(
                        parameter.numel()
                        for parameter in model.parameters()
                        if parameter.requires_grad
                    ),
                    "best_epoch": best_epoch,
                    "validation_metrics": validation_metrics,
                    "holdout_metrics": holdout_metrics,
                    "single_prediction_benchmark": benchmark,
                }
                diagnostics = getattr(model, "diagnostics", None)
                if diagnostics is not None:
                    run["models"][model_name]["model_diagnostics"] = diagnostics()
                if isinstance(model, BagAblationModel):
                    selected_bag = model
                if isinstance(model, AppCurrentChoiceModel):
                    selected_app = model
                print(
                    f"  fold {fold + 1} seed {seed:>2} {model_name:<13} "
                    f"epoch {best_epoch:>2} | NLL "
                    f"{holdout_metrics['negative_log_likelihood']:.4f} | "
                    f"top-1 {holdout_metrics['top_1_accuracy']:.3%}",
                    flush=True,
                )
            runs.append(run)

    payload = {
        "poc_schema_version": 1,
        "experiment": "rolling_chronological_multi_seed",
        "pytorch_version": torch.__version__,
        "device": str(device),
        "config": {
            **vars(args),
            "output": str(args.output.resolve()),
            "models": model_names,
            "seeds": seeds,
        },
        "runs": runs,
        "aggregate": aggregate_results(runs, model_names),
    }
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output}", flush=True)


if __name__ == "__main__":
    main()
