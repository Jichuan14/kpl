#!/usr/bin/env python3
"""Fit one global temperature to saved calibration predictions."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

from calibration import fit_global_temperature, load_prediction_records, read_manifest, series_balanced_nll


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(value, tmp, ensure_ascii=False, indent=2, allow_nan=False)
        tmp.write("\n")
        temporary = Path(tmp.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = read_manifest(args.manifest)
    records = load_prediction_records(args.predictions)
    fitted = fit_global_temperature(records)
    artifact = {
        "schema_version": 1,
        "method": "global_temperature",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy_model_type": metadata["policy_model_type"],
        "model_fingerprint": metadata["model_fingerprint"],
        "candidate_policy_id": metadata["candidate_policy_id"],
        "candidate_policy_fingerprint": metadata["candidate_policy_fingerprint"],
        "context_contract_version": metadata.get("context_contract_version", "bp_context_v1"),
        "context_validation_mode": metadata.get("context_validation_mode", "not_applicable"),
        "calibration_match_ids": sorted(set(records.series_ids.tolist())),
        "model_training_match_ids_sha256": metadata["model_training_match_ids_sha256"],
        "split_manifest_sha256": metadata["split_manifest_sha256"],
        "calibration_decisions": int(len(records.targets)),
        **fitted,
    }
    if fitted["status"] == "experimental":
        artifact["metrics"]["calibration"]["series_balanced_nll"] = series_balanced_nll(records, fitted["temperature"])
    atomic_json(args.output.resolve(), artifact)
    print(json.dumps({"output": str(args.output.resolve()), "status": artifact["status"], "temperature": artifact["temperature"], "metrics": artifact["metrics"]}, indent=2))


if __name__ == "__main__":
    main()
