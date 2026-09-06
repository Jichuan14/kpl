#!/usr/bin/env python3
"""Train the production familiarity residual on a frozen sequence checkpoint."""

from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np

ANALYSIS = Path(__file__).resolve().parent; ROOT = ANALYSIS.parent
sys.path.insert(0, str(ANALYSIS / "sequence_training"))

from calibration import PredictionRecords, score_metrics  # noqa: E402
from models import ACTION_INDEX, load_checkpoint, prepare_data, seed_everything  # noqa: E402
from personalized_models import FamiliarityResidual  # noqa: E402
from player_context.dataset import candidate_familiarity_features, compact_familiarity_context  # noqa: E402
from player_context.history import TemporalContextBuilder, load_historical_games  # noqa: E402
from sequence_training.splits import validate_split_manifest  # noqa: E402


def phase(position: int) -> str:
    return "opening_bans_and_first_pick" if position <= 5 else "first_pick_phase" if position <= 10 else "second_ban_phase" if position <= 16 else "closing_picks"


def source_rows(seasons: list[str]) -> dict[tuple[str,str,int],dict[str,Any]]:
    dates = {}
    for season in seasons:
        with (ANALYSIS/"exports"/season/"matches.jsonl").open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    row=json.loads(line); dates[str(row["match_id"])]=date.fromisoformat(str(row["start_time"])[:10])
    rows={}
    for season in seasons:
        with (ANALYSIS/"exports"/season/"bp_decisions.jsonl").open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    row=json.loads(line)
                    key=(str(row["match_id"]),str(row["battle_id"]),int(row["bp_order"]))
                    rows[key]={name:row.get(name,[]) for name in (
                        "current_team_picks","current_opponent_picks","team_used_in_previous_battles","opponent_used_in_previous_battles"
                    )}
                    rows[key].update(event_date=dates[key[0]].isoformat(),acting_team_id=str(row["acting_team_id"]),opponent_team_id=str(row["opponent_team_id"]))
    return rows


class Extended:
    def __init__(self, base: Any, extras: dict[str, Any]): self.base,self.extras=base,extras
    def __len__(self): return len(self.base)
    def batch(self, indices: Any, device: Any) -> dict[str, Any]:
        import torch
        result=self.base.batch(indices,device)
        result.update({name:value.index_select(0,indices).to(device=device,dtype=torch.float32) for name,value in self.extras.items()})
        return result


def extend(base: Any, rows: dict[tuple[str,str,int],dict[str,Any]], builder: TemporalContextBuilder) -> Extended:
    import torch
    features=np.empty((len(base),len(builder.hero_ids),9),dtype=np.float16)
    cache={}
    for index,(match,battle,position) in enumerate(zip(base.match_ids,base.battle_ids,base.next_positions.tolist(),strict=True)):
        row=rows[(match,battle,int(position))]; cutoff=date.fromisoformat(row["event_date"])
        key=(str(row["acting_team_id"]),str(row["opponent_team_id"]),cutoff)
        context=cache.get(key)
        if context is None:
            context=compact_familiarity_context(builder.build(key[0],key[1],cutoff));cache[key]=context
        features[index]=candidate_familiarity_features(context,own_picks=[int(v) for v in row.get("current_team_picks",[])],opponent_picks=[int(v) for v in row.get("current_opponent_picks",[])],own_previous=row.get("team_used_in_previous_battles",[]),opponent_previous=row.get("opponent_used_in_previous_battles",[]),hero_ids=list(builder.hero_ids))
    return Extended(base,{"candidate_features":torch.from_numpy(features)})


def logits_for(base_model: Any, branch: Any, dataset: Extended, batch_size: int, device: Any) -> np.ndarray:
    import torch
    output=[];base_model.eval();branch.eval()
    with torch.inference_mode():
        for start in range(0,len(dataset),batch_size):
            indices=torch.arange(start,min(start+batch_size,len(dataset))); batch=dataset.batch(indices,device)
            base=base_model(batch); output.append(branch(base,batch["candidate_features"],batch["next_actions"],batch["legal_mask"]).cpu().numpy())
    return np.concatenate(output)


def records(dataset: Extended, logits: np.ndarray) -> PredictionRecords:
    base=dataset.base; positions=base.next_positions.numpy()
    return PredictionRecords(logits.astype(np.float64),base.legal_mask.numpy(),base.targets.numpy(),np.asarray(base.match_ids),np.where(base.next_actions.numpy()==1,"pick","ban"),np.asarray([phase(int(p)) for p in positions]))


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint",type=Path,required=True);parser.add_argument("--split-manifest",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--seed",type=int,default=7);parser.add_argument("--epochs",type=int,default=30);parser.add_argument("--batch-size",type=int,default=128)
    parser.add_argument("--learning-rate",type=float,default=.001);parser.add_argument("--weight-decay",type=float,default=.0001);parser.add_argument("--threads",type=int,default=4)
    args=parser.parse_args(); import torch
    torch.set_num_threads(args.threads);seed_everything(args.seed);device=torch.device("cpu")
    manifest=json.loads(args.split_manifest.read_text());validate_split_manifest(manifest);seasons=[str(v) for v in manifest["source_seasons"]]
    data=prepare_data(ROOT,target_season=str(manifest["target_season"]),previous_seasons=len(seasons)-1,validation_matches=len(manifest["splits"]["validation"]),holdout_matches=len(manifest["splits"]["calibration"])+len(manifest["splits"]["holdout"]),holdout_offset_matches=int(manifest.get("holdout_offset_series",0)),recency_decay=.65,winning_pick_weight=1.5)
    base=load_checkpoint(args.checkpoint,device); payload=torch.load(args.checkpoint,map_location="cpu",weights_only=False)
    if payload["hero_ids"]!=data.hero_ids or payload["team_ids"]!=data.team_ids: raise ValueError("Checkpoint and manifest vocabularies differ")
    raw=source_rows(seasons);games=load_historical_games([ANALYSIS/"exports"/season/"matches.jsonl" for season in seasons]);builder=TemporalContextBuilder(games,data.hero_ids)
    train,validation,holdout=extend(data.train,raw,builder),extend(data.validation,raw,builder),extend(data.holdout,raw,builder)
    branch=FamiliarityResidual()
    branch.to(device);optimizer=torch.optim.AdamW(branch.parameters(),lr=args.learning_rate,weight_decay=args.weight_decay);generator=torch.Generator().manual_seed(args.seed)
    best=None;best_nll=math.inf;history=[];stale=0
    for epoch in range(1,args.epochs+1):
        branch.train(); order=torch.randperm(len(train),generator=generator);weighted_sum=0.;weight_sum=0.
        for start in range(0,len(train),args.batch_size):
            index=order[start:start+args.batch_size];batch=train.batch(index,device);optimizer.zero_grad(set_to_none=True)
            with torch.no_grad(): base_logits=base(batch)
            output=branch(base_logits,batch["candidate_features"],batch["next_actions"],batch["legal_mask"])
            losses=torch.nn.functional.cross_entropy(output,batch["targets"],reduction="none");loss=(losses*batch["sample_weights"]).sum()/batch["sample_weights"].sum()+branch.regularization_loss();loss.backward();torch.nn.utils.clip_grad_norm_(branch.parameters(),1.0);optimizer.step()
            weighted_sum+=float((losses*batch["sample_weights"]).sum().detach());weight_sum+=float(batch["sample_weights"].sum())
        validation_logits=logits_for(base,branch,validation,args.batch_size,device);metrics=score_metrics(records(validation,validation_logits));nll=metrics["negative_log_likelihood"]
        history.append({"epoch":epoch,"training_weighted_nll":weighted_sum/weight_sum,"validation":metrics})
        print(f"epoch {epoch}: train {weighted_sum/weight_sum:.4f} validation {nll:.4f}",flush=True)
        if nll<best_nll-1e-7: best_nll=nll;best=copy.deepcopy(branch.state_dict());stale=0
        else: stale+=1
        if stale>=5: break
    if best is None: raise AssertionError("No selected checkpoint")
    branch.load_state_dict(best); output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    torch.save({"schema_version":1,"model_type":branch.model_name,"config":{"feature_width":9},"state_dict":branch.state_dict(),"base_checkpoint":str(args.checkpoint.resolve()),"hero_ids":data.hero_ids,"team_ids":data.team_ids,"player_vocab":{},"split_manifest_sha256":manifest["manifest_sha256"],"seed":args.seed},output/f"familiarity_seed{args.seed}.pt")
    c_ids={str(r["match_id"]) for r in manifest["splits"]["calibration"]};h_ids={str(r["match_id"]) for r in manifest["splits"]["holdout"]}
    all_logits=logits_for(base,branch,holdout,args.batch_size,device);all_records=records(holdout,all_logits)
    results={"model":"familiarity","seed":args.seed,"parameters":sum(p.numel() for p in branch.parameters()),"best_epoch":min(history,key=lambda r:r["validation"]["negative_log_likelihood"])["epoch"],"history":history,"splits":{}}
    for name,ids in (("calibration",c_ids),("holdout",h_ids)):
        selected=np.flatnonzero(np.isin(all_records.series_ids,list(ids))); rec=PredictionRecords(all_records.logits[selected],all_records.accepted_mask[selected],all_records.targets[selected],all_records.series_ids[selected],all_records.actions[selected],all_records.phases[selected])
        np.savez_compressed(output/f"{name}_predictions.npz",logits=rec.logits.astype(np.float32),accepted_mask=rec.accepted_mask,targets=rec.targets,series_ids=rec.series_ids,actions=rec.actions,phases=rec.phases);results["splits"][name]=score_metrics(rec)
    (output/"results.json").write_text(json.dumps(results,ensure_ascii=False,indent=2)+"\n");print(json.dumps(results["splits"],indent=2))


if __name__=="__main__": main()
