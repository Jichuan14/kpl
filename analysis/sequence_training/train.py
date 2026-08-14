#!/usr/bin/env python3
"""Train and compare sequence-aware draft-choice models."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from models import (
    AppCurrentChoiceModel,
    BagAblationModel,
    HybridAppLagGRUModel,
    HybridBagGRUModel,
    MODEL_TYPES,
    ModelConfig,
    PairwiseResponseModel,
    STRATEGIC_CONSTRAINT_VERSION,
    benchmark_single_prediction,
    find_repo_root,
    pairwise_example,
    prepare_data,
    save_checkpoint,
    seed_everything,
    train_model,
)


TRAINING_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-season", default="20260003")
    parser.add_argument("--previous-seasons", type=int, default=4)
    parser.add_argument("--validation-matches", type=int, default=10)
    parser.add_argument("--holdout-matches", type=int, default=10)
    parser.add_argument("--holdout-offset-matches", type=int, default=0)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--hidden-dim", type=int, default=48)
    parser.add_argument("--learning-rate", type=float, default=0.003)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--recency-decay", type=float, default=0.65)
    parser.add_argument("--winning-pick-weight", type=float, default=1.5)
    parser.add_argument("--seed", type=int, default=7)
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
        "--output-dir", type=Path, default=TRAINING_DIR / "artifacts"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model_names = [name.strip() for name in args.models.split(",") if name.strip()]
    unknown = sorted(set(model_names) - set(MODEL_TYPES))
    if unknown:
        raise ValueError(f"Unknown models: {unknown}")
    if not model_names:
        raise ValueError("At least one model is required")
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
    seed_everything(args.seed)
    device = torch.device("cpu")
    repo_root = find_repo_root(TRAINING_DIR)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("PyTorch:", torch.__version__)
    print("Device:", device)
    print("Preparing chronological data...")
    data = prepare_data(
        repo_root,
        target_season=args.target_season,
        previous_seasons=args.previous_seasons,
        validation_matches=args.validation_matches,
        holdout_matches=args.holdout_matches,
        holdout_offset_matches=args.holdout_offset_matches,
        recency_decay=args.recency_decay,
        winning_pick_weight=args.winning_pick_weight,
    )
    print(
        f"Train decisions: {len(data.train):,}; "
        f"validation decisions: {len(data.validation):,}; "
        f"holdout decisions: {len(data.holdout):,}; "
        f"validation/holdout matches: {len(data.validation_match_ids)}/"
        f"{len(data.holdout_match_ids)}"
    )
    config = ModelConfig(
        hero_count=len(data.hero_ids),
        team_count=len(data.team_ids),
        feature_width=int(data.hero_features.shape[1]),
        hidden_dim=args.hidden_dim,
    )
    run_results: dict[str, object] = {
        "training_schema_version": 1,
        "pytorch_version": torch.__version__,
        "device": str(device),
        "target_season": args.target_season,
        "training_seasons": data.training_seasons,
        "season_weights": data.season_weights,
        "validation_match_ids": data.validation_match_ids,
        "holdout_match_ids": data.holdout_match_ids,
        "excluded_future_match_ids": data.excluded_future_match_ids,
        "train_decisions": len(data.train),
        "validation_decisions": len(data.validation),
        "holdout_decisions": len(data.holdout),
        "strategic_constraints": {
            "version": STRATEGIC_CONSTRAINT_VERSION,
            "second_ban_farm_only_conflicts": True,
        },
        "config": {
            **vars(args),
            "output_dir": str(output_dir),
        },
        "models": {},
    }

    selected_bag: BagAblationModel | None = None
    selected_app: AppCurrentChoiceModel | None = None
    for offset, model_name in enumerate(model_names):
        seed_everything(args.seed + offset)
        model = MODEL_TYPES[model_name](config, data.hero_features)
        if isinstance(model, HybridBagGRUModel):
            if selected_bag is None:
                raise AssertionError("The hybrid model has no selected bag checkpoint")
            model.initialize_from_bag(selected_bag)
        if isinstance(model, HybridAppLagGRUModel):
            if selected_app is None:
                raise AssertionError("The hybrid model has no selected app checkpoint")
            model.initialize_from_app(selected_app)
        parameter_count = sum(parameter.numel() for parameter in model.parameters())
        trainable_parameter_count = sum(
            parameter.numel()
            for parameter in model.parameters()
            if parameter.requires_grad
        )
        print(f"\nTraining {model_name} ({parameter_count:,} parameters)")
        model, training_history, validation_metrics, holdout_metrics = train_model(
            model,
            data.train,
            data.validation,
            data.holdout,
            epochs=args.epochs,
            batch_size=getattr(model, "recommended_batch_size", args.batch_size),
            learning_rate=getattr(
                model, "recommended_learning_rate", args.learning_rate
            ),
            weight_decay=args.weight_decay,
            device=device,
            seed=args.seed + offset,
        )
        latency = benchmark_single_prediction(model, data.holdout, device=device)
        checkpoint_path = output_dir / f"{model_name}.pt"
        save_checkpoint(checkpoint_path, model, data, holdout_metrics)
        model_result: dict[str, object] = {
            "parameters": parameter_count,
            "trainable_parameters": trainable_parameter_count,
            "best_epoch": min(
                training_history,
                key=lambda row: row["validation_negative_log_likelihood"],
            )["epoch"],
            "training_history": training_history,
            "validation_metrics": validation_metrics,
            "holdout_metrics": holdout_metrics,
            "single_prediction_benchmark": latency,
            "checkpoint": str(checkpoint_path),
        }
        diagnostics = getattr(model, "diagnostics", None)
        if diagnostics is not None:
            model_result["model_diagnostics"] = diagnostics()
        if isinstance(model, PairwiseResponseModel):
            example = pairwise_example(model, data.holdout, data, device=device)
            model_result["attribution_example"] = example
            (output_dir / "pairwise_attribution_example.json").write_text(
                json.dumps(example, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        run_results["models"][model_name] = model_result
        if isinstance(model, BagAblationModel):
            selected_bag = model
        if isinstance(model, AppCurrentChoiceModel):
            selected_app = model
        print(
            f"{model_name}: holdout NLL "
            f"{holdout_metrics['negative_log_likelihood']:.4f}; "
            f"top-1 {holdout_metrics['top_1_accuracy']:.3%}; "
            f"top-5 {holdout_metrics['top_5_accuracy']:.3%}; "
            f"{latency['mean_milliseconds']:.3f} ms/prediction"
        )

    results_path = output_dir / "results.json"
    results_path.write_text(
        json.dumps(run_results, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"\nWrote {results_path}")


if __name__ == "__main__":
    main()
