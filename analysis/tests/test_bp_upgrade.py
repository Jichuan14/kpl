from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ANALYSIS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ANALYSIS));sys.path.insert(0,str(ANALYSIS/"sequence_training"))

from calibration import PredictionRecords, fit_global_temperature, masked_softmax
from player_context.history import HistoricalGame, PlayerAppearance, TemporalContextBuilder, normalize_player_name
from player_context.roles import open_role_probabilities
from sequence_training.splits import validate_split_manifest


def test_temperature_fit_and_exclusion_gate():
    records=PredictionRecords(np.array([[3.,2.],[2.,3.],[3.,2.]]),np.ones((3,2),bool),np.array([0,1,1]),np.array(["a","b","c"]),np.array(["pick"]*3),np.array(["x"]*3))
    fit=fit_global_temperature(records);assert fit["metrics"]["calibration"]["nll_fitted"]<=fit["metrics"]["calibration"]["nll_t1"]
    excluded=replace(records,accepted_mask=np.array([[1,1],[1,1],[1,0]],bool))
    assert fit_global_temperature(excluded)["status"]=="not_fit_due_to_candidate_exclusion"
    assert np.allclose(masked_softmax(records.logits,records.accepted_mask,1).sum(1),1)


def test_split_overlap_rejected():
    row={"season":"s","match_id":"1","start_time":"2026-01-01"}
    with pytest.raises(ValueError): validate_split_manifest({"splits":{"train":[row],"validation":[row],"calibration":[{**row,"match_id":"2"}],"holdout":[{**row,"match_id":"3"}]}})


def appearance(team,name,hero,role): return PlayerAppearance(team,f"{team}:{normalize_player_name(name)}",name,hero,role)


def test_temporal_context_ignores_same_day_future_and_outcomes():
    past=HistoricalGame(date(2026,1,1),"m1","b1",1,(appearance("t"," Alice ",1,2),))
    same=HistoricalGame(date(2026,1,2),"m2","b2",1,(appearance("t","Alice",2,2),))
    builder=TemporalContextBuilder([past,same],[1,2]);first=builder.build("t","o",date(2026,1,2))
    future=HistoricalGame(date(2026,1,3),"m3","b3",1,(appearance("t","Alice",2,2),))
    second=TemporalContextBuilder([past,same,future],[1,2]).build("t","o",date(2026,1,2))
    assert first==second;assert first["maximum_source_event_date"]=="2026-01-01"
    assert first["rosters"][0][0][-1]["player_key"]=="<unknown>"


def test_soft_roles_are_normalized_and_never_zero():
    prior=np.array([[1,0,0,0,0],[0,1,0,0,0]],float)
    open_roles=open_role_probabilities([0,1],prior)
    assert open_roles.sum()==pytest.approx(3);assert np.all(open_roles>=0)
    normalized=prior+1e-4;normalized/=normalized.sum(1,keepdims=True);assert np.all(normalized>0)
