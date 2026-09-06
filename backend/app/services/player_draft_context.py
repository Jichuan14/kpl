"""Load pinned player-context snapshots and derive dynamic soft-role features."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import itertools
import numpy as np

_CACHE:dict[Path,tuple[int,dict[str,Any]]]={}


def load_context_snapshot(path:Path,hero_ids:list[int])->dict[str,Any]:
    modified=path.stat().st_mtime_ns;cached=_CACHE.get(path)
    if cached and cached[0]==modified:return cached[1]
    value=json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version")!=1 or value.get("context_contract_version")!="player_context_v1" or [int(v) for v in value.get("hero_ids",[])]!=hero_ids:raise ValueError("Player context snapshot is incompatible")
    value["_runtime_pairs"]={};value["_open_cache"]={};value["_feature_cache"]={}
    _CACHE[path]=(modified,value);return value


def _open(picks:list[int],prior:np.ndarray,hero_to_index:dict[int,int])->np.ndarray:
    indices=[hero_to_index[v] for v in picks if v in hero_to_index]
    if not indices:return np.ones(5)
    assignments=list(itertools.permutations(range(5),len(indices)));logs=np.asarray([sum(np.log(prior[h,r]) for h,r in zip(indices,a)) for a in assignments]);weights=np.exp(logs-logs.max());weights/=weights.sum();result=np.zeros(5)
    for weight,assignment in zip(weights,assignments):result+=weight*np.asarray([role not in assignment for role in range(5)])
    return result


def candidate_features(snapshot:dict[str,Any],own_team:str,opponent_team:str,state:dict[str,Any],side:str)->tuple[np.ndarray,str]|tuple[None,str]:
    pair_key=f"{own_team}|{opponent_team}"
    pair=snapshot.get("pairs",{}).get(pair_key)
    if pair is None:return None,"team_pair_missing"
    runtime=snapshot.setdefault("_runtime_pairs",{}).get(pair_key)
    heroes=[int(v) for v in snapshot["hero_ids"]]
    if runtime is None:
        index={v:i for i,v in enumerate(heroes)};prior=np.asarray(pair["hero_role_prior"],dtype=np.float64);prior=np.maximum(prior,1e-8);prior/=prior.sum(1,keepdims=True)
        runtime=(index,prior,np.asarray(pair["familiarity"]),float(sum(pair["coverage"])/2));snapshot["_runtime_pairs"][pair_key]=runtime
    index,prior,fam,coverage=runtime
    other="red" if side=="blue" else "blue";own_picks=tuple(int(v) for v in state.get(f"{side}_picks",[]));opp_picks=tuple(int(v) for v in state.get(f"{other}_picks",[]));own_previous_values=tuple(int(v) for v in state.get(f"{side}_used_previous_battles",[]));opp_previous_values=tuple(int(v) for v in state.get(f"{other}_used_previous_battles",[]));feature_key=(pair_key,own_picks,opp_picks,own_previous_values,opp_previous_values)
    cached_features=snapshot.setdefault("_feature_cache",{}).get(feature_key)
    if cached_features is not None:return cached_features,"ok"
    cache=snapshot.setdefault("_open_cache",{})
    own_cache_key=(pair_key,own_picks);opp_cache_key=(pair_key,opp_picks)
    own_open=cache.get(own_cache_key)
    if own_open is None:own_open=_open(list(own_picks),prior,index);cache[own_cache_key]=own_open
    opp_open=cache.get(opp_cache_key)
    if opp_open is None:opp_open=_open(list(opp_picks),prior,index);cache[opp_cache_key]=opp_open
    own_norm=own_open/own_open.sum() if own_open.sum() else own_open;opp_norm=opp_open/opp_open.sum() if opp_open.sum() else opp_open
    own_aff=own_norm@fam[0];opp_aff=opp_norm@fam[1];own_fit=prior@own_open;opp_fit=prior@opp_open
    hero_array=np.asarray(heroes);own_previous=np.isin(hero_array,np.asarray(own_previous_values,dtype=np.int64));opp_previous=np.isin(hero_array,np.asarray(opp_previous_values,dtype=np.int64))
    count=len(heroes)
    result=np.column_stack((own_aff,opp_aff,own_fit,opp_fit,own_previous,opp_previous,np.full(count,coverage),np.full(count,float(own_open.sum()==0)),np.full(count,float(opp_open.sum()==0)))).astype(np.float32)
    snapshot["_feature_cache"][feature_key]=result
    return result,"ok"
