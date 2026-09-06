#!/usr/bin/env python3
"""Bind personalized prediction arrays to an exported candidate and split policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--artifact",type=Path,required=True);parser.add_argument("--base-prediction-manifest",type=Path,required=True);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    artifact=json.loads(args.artifact.read_text(encoding="utf-8"));base=json.loads(args.base_prediction_manifest.read_text(encoding="utf-8"))
    value={key:base[key] for key in ("candidate_policy_id","candidate_policy_fingerprint","split_manifest_sha256","model_training_match_ids_sha256")}
    value.update({"schema_version":1,"policy_model_type":"personalized","model_fingerprint":artifact["model_fingerprint"],"context_contract_version":artifact["context_contract_version"],"context_validation_mode":"rolling_as_of_snapshots","artifact":str(args.artifact.resolve())})
    args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(f"Wrote {args.output.resolve()}")


if __name__=="__main__":main()
