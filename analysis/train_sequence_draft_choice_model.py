"""Train and export the chronological bag-plus-GRU model for the web app.

Run this command in a separate PyTorch environment.  The produced schema-v3
JSON artifact is consumed by the NumPy-only backend runtime.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    for candidate in (start, *start.parents):
        if (candidate / "analysis" / "sequence_training" / "train.py").is_file():
            return candidate
    raise FileNotFoundError("Could not find the repository root")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--previous-seasons", type=int, default=4)
    parser.add_argument("--validation-matches", type=int, default=10)
    parser.add_argument("--holdout-matches", type=int, default=10)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def run(command: list[str], repo_root: Path) -> None:
    print("Running:", " ".join(command), flush=True)
    subprocess.run(command, cwd=repo_root, check=True)


def main() -> None:
    args = parse_args()
    if not args.league_id or not all(
        character.isalnum() or character in "-_" for character in args.league_id
    ):
        raise ValueError("Invalid league id")
    if min(
        args.previous_seasons,
        args.validation_matches,
        args.holdout_matches,
        args.epochs,
        args.threads,
    ) < 1:
        raise ValueError("Training counts must all be positive")

    repo_root = find_repo_root(Path(__file__).resolve())
    output_dir = (
        repo_root
        / "analysis"
        / "outputs"
        / args.league_id
        / "sequence_training"
    )
    artifact_path = (
        args.output
        or repo_root
        / "analysis"
        / "outputs"
        / args.league_id
        / "sequence_draft_choice_model.json"
    ).resolve()
    trainer = repo_root / "analysis" / "sequence_training" / "train.py"
    exporter = repo_root / "analysis" / "export_sequence_draft_choice_model.py"
    run(
        [
            sys.executable,
            str(trainer),
            "--target-season",
            args.league_id,
            "--previous-seasons",
            str(args.previous_seasons),
            "--validation-matches",
            str(args.validation_matches),
            "--holdout-matches",
            str(args.holdout_matches),
            "--epochs",
            str(args.epochs),
            "--seed",
            str(args.seed),
            "--threads",
            str(args.threads),
            "--models",
            "bag_ablation,hybrid_bag_gru",
            "--output-dir",
            str(output_dir),
        ],
        repo_root,
    )
    run(
        [
            sys.executable,
            str(exporter),
            "--league-id",
            args.league_id,
            "--checkpoint",
            str(output_dir / "hybrid_bag_gru.pt"),
            "--experiment-results",
            str(output_dir / "results.json"),
            "--output",
            str(artifact_path),
        ],
        repo_root,
    )
    print(f"Sequence model is ready for the web app: {artifact_path}")


if __name__ == "__main__":
    main()
