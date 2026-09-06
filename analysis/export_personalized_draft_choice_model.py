#!/usr/bin/env python3
"""Export a self-contained production sequence + familiarity policy."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def tensor_json(value: Any) -> Any:
    array=value.detach().cpu(); return float(array.item()) if array.ndim==0 else array.tolist()


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--checkpoint",type=Path,required=True);parser.add_argument("--base-artifact",type=Path,required=True);parser.add_argument("--calibration-sidecar",type=Path);parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
    import torch
    checkpoint=torch.load(args.checkpoint,map_location="cpu",weights_only=False);base=json.loads(args.base_artifact.read_text(encoding="utf-8"))
    if base.get("model_type")!="frozen_bag_gru_residual_choice": raise ValueError("Base artifact is not a sequence model")
    if checkpoint.get("model_type") != "familiarity_residual": raise ValueError("Only the selected familiarity residual can be exported")
    parameters={name:tensor_json(value) for name,value in sorted(checkpoint["state_dict"].items())}
    feature_path=Path(__file__).resolve().parent/base["feature_artifact"]
    feature=json.loads(feature_path.read_text(encoding="utf-8"));width=len(base["feature_names"])
    by_id={int(row["hero_id"]):[*row["vector"],float(row.get("feature_known",True))] for row in feature["rows"]}
    feature_matrix=[by_id.get(int(hero),[0.0]*width) for hero in base["hero_ids"]]
    identity={"candidate_kind":checkpoint["model_type"],"config":checkpoint["config"],"hero_ids":checkpoint["hero_ids"],"team_ids":checkpoint["team_ids"],"player_vocab":checkpoint["player_vocab"],"parameters":parameters,"base_parameters_sha256":base["parameters_sha256"],"split_manifest_sha256":checkpoint["split_manifest_sha256"]}
    artifact={"schema_version":1,"model_type":"sequence_familiarity_residual_choice","candidate_kind":checkpoint["model_type"],"status":"production","generated_at":datetime.now(timezone.utc).isoformat(),**identity,
              "model_fingerprint":hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()).hexdigest(),"base_artifact":base,
              "hero_feature_matrix":feature_matrix,
              "context_contract_version":"player_context_v1","calibration":{"status":"uncalibrated","temperature":1.0,"reason":"new_composite_weights_require_independent_calibration"}}
    if args.calibration_sidecar:
        sidecar=json.loads(args.calibration_sidecar.read_text(encoding="utf-8"))
        if sidecar.get("model_fingerprint") != artifact["model_fingerprint"] or sidecar.get("status") != "eligible": raise ValueError("Calibration sidecar is not eligible for this exact model")
        artifact["calibration"]={"status":"eligible","temperature":float(sidecar["temperature"]),"sidecar":args.calibration_sidecar.name}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile("w",encoding="utf-8",dir=args.output.parent,delete=False) as tmp:
        json.dump(artifact,tmp,ensure_ascii=False,separators=(",",":"));tmp.write("\n");temporary=Path(tmp.name)
    temporary.replace(args.output);print(f"Wrote {args.output.resolve()}")


if __name__=="__main__": main()
