import json

import numpy as np
import pytest

from app.services.draft_calibration import (
    candidate_policy_fingerprint,
    masked_softmax,
    resolve_calibration,
    semantic_model_fingerprint,
)


def test_masked_softmax_contract():
    logits=np.array([10000.0,9999.0,-5.0],dtype=np.float32);mask=np.array([True,True,False])
    original=masked_softmax(logits,mask,1.0);warmer=masked_softmax(logits,mask,2.0)
    assert original.sum()==pytest.approx(1);assert original[2]==0;assert warmer[0]>warmer[1]
    assert masked_softmax(np.array([1.0]),np.array([True]),0.5)[0]==1
    for invalid in (0,-1,float("inf"),float("nan")):
        with pytest.raises(ValueError): masked_softmax(logits,mask,invalid)
    with pytest.raises(ValueError): masked_softmax(logits,np.zeros(3,dtype=bool))


def test_sidecar_identity_and_safe_fallback(tmp_path):
    model={"schema_version":4,"model_type":"x","target_season":"s","config":{},"parameters":{"x":[1]},"hero_ids":[1],"team_ids":["t"],"feature_names":["f"]}
    fingerprint=semantic_model_fingerprint(model,np.array([[1]],dtype=np.float32));policy=candidate_policy_fingerprint("legacy_role_filter_v1",role_map={"1":1})
    path=tmp_path/"draft_probability_calibration.json"
    path.write_text(json.dumps({"schema_version":1,"method":"global_temperature","status":"experimental","policy_model_type":"sequence","temperature":1.2,"model_fingerprint":fingerprint,"candidate_policy_id":"legacy_role_filter_v1","candidate_policy_fingerprint":policy}))
    resolved=resolve_calibration(path,enabled_mode="experimental",policy_model_type="sequence",model_fingerprint=fingerprint,candidate_policy_id="legacy_role_filter_v1",candidate_policy_fingerprint_value=policy)
    assert resolved.temperature==1.2 and resolved.diagnostic=="ok"
    assert resolve_calibration(path,enabled_mode="eligible",policy_model_type="sequence",model_fingerprint=fingerprint,candidate_policy_id="legacy_role_filter_v1",candidate_policy_fingerprint_value=policy).temperature==1
    assert resolve_calibration(path,enabled_mode="experimental",policy_model_type="sequence",model_fingerprint=fingerprint+"x",candidate_policy_id="legacy_role_filter_v1",candidate_policy_fingerprint_value=policy).diagnostic=="model_fingerprint_mismatch"
    model["parameters"]["x"]=[2]
    assert semantic_model_fingerprint(model,np.array([[1]],dtype=np.float32))!=fingerprint

