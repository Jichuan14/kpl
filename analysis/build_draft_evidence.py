#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from draft_evidence.builder import build


def main() -> int:
    parser = argparse.ArgumentParser(description="Build deterministic all-season historical draft evidence.")
    parser.add_argument("--league-id", required=True)
    parser.add_argument("--exports-root", type=Path, default=Path("analysis/exports"))
    parser.add_argument("--output-root", type=Path, default=Path("analysis/outputs"))
    parser.add_argument("--mode", choices=("retrospective_all_available", "as_of_target_match"), default="retrospective_all_available")
    args = parser.parse_args()
    result = build(exports_root=args.exports_root, output_root=args.output_root, target_league_id=args.league_id, mode=args.mode)
    print(json.dumps({"corpus_id": result["corpus_id"], "matches": result["match_count"], "moves": result["move_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

