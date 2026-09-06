#!/usr/bin/env python3
"""Create a pinned chronological train/V/C/H series manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from sequence_training.splits import build_split_manifest, write_split_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-season", required=True)
    parser.add_argument("--source-seasons", required=True, help="Comma-separated pinned list")
    parser.add_argument("--validation-series", type=int, default=10)
    parser.add_argument("--calibration-series", type=int, default=10)
    parser.add_argument("--holdout-series", type=int, default=10)
    parser.add_argument("--holdout-offset-series", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = build_split_manifest(
        root / "analysis" / "exports",
        target_season=args.target_season,
        source_seasons=[value.strip() for value in args.source_seasons.split(",") if value.strip()],
        validation_series=args.validation_series,
        calibration_series=args.calibration_series,
        holdout_series=args.holdout_series,
        holdout_offset_series=args.holdout_offset_series,
    )
    write_split_manifest(args.output.resolve(), manifest)
    print(f"Wrote {args.output.resolve()}")
    print(manifest["counts"])


if __name__ == "__main__":
    main()
