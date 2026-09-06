"""NumPy-only inference for the selected familiarity residual."""

from __future__ import annotations

from typing import Any

import numpy as np
from numpy.typing import NDArray


Array = NDArray[np.float32]


def _arrays(raw: dict[str, Any]) -> dict[str, Array]:
    result={name:np.asarray(value,dtype=np.float32) for name,value in raw.items()}
    if any(not np.isfinite(value).all() for value in result.values()): raise ValueError("Personalized parameters contain non-finite values")
    return result


def prepare_personalized_parameters(artifact: dict[str, Any]) -> dict[str, Any]:
    if artifact.get("schema_version")!=1 or artifact.get("model_type") != "sequence_familiarity_residual_choice": raise ValueError("Unsupported familiarity model")
    kind=str(artifact.get("candidate_kind")); params=_arrays(artifact.get("parameters",{})); config=dict(artifact.get("config",{}))
    if kind != "familiarity_residual": raise ValueError("Unsupported familiarity candidate kind")
    return {"kind":kind,"parameters":params,"config":config,"hero_count":len(artifact.get("hero_ids",[])),"model_fingerprint":artifact.get("model_fingerprint")}


def _linear(x: Array,p:dict[str,Array],name:str) -> Array:
    result=x@p[f"{name}.weight"].T
    bias=p.get(f"{name}.bias"); return result+(bias if bias is not None else 0)


def _bounded(base:Array,raw:Array,legal:NDArray[np.bool_],coverage:Array)->Array:
    count=np.maximum(legal.sum(axis=1,keepdims=True),1);centered=raw-np.where(legal,raw,0).sum(axis=1,keepdims=True)/count
    return np.where(legal,base+coverage*np.tanh(centered),np.float32(-1e9)).astype(np.float32)


def personalized_logits(prepared:dict[str,Any],batch:dict[str,Any],base_logits:Array)->Array:
    p=prepared["parameters"];legal=np.asarray(batch["legal_mask"],dtype=np.bool_);features=np.asarray(batch["candidate_features"],dtype=np.float32);base=np.asarray(base_logits,dtype=np.float32)
    pick=_linear(features,p,"pick_head")[...,0];ban=_linear(features,p,"ban_head")[...,0];raw=np.where(np.asarray(batch["next_actions"])[:,None]==1,pick,ban)
    return _bounded(base,raw,legal,features[:,0,6:7])
