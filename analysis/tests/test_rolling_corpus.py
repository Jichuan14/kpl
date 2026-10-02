import copy
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys
import numpy as np
import pytest
ANALYSIS=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ANALYSIS))
from rolling_corpus import build_rolling_manifest,DeferredTraining,event_time,series_weight,training_context_games,exported_battles
from sequence_training.splits import validate_split_manifest,_take_date_grouped_tail
from lineup_value.history import fit_logistic
from train_lineup_value_model import load_trainer


def exports(root,count=50):
    for index in range(count):
        season='A' if index<25 else 'B';match=f'm{index:03d}';battle=f'b{index:03d}'
        time=(datetime(2025,1,1)+timedelta(days=index)).isoformat()
        path=root/season;path.mkdir(exist_ok=True,parents=True)
        players=[{'camp':camp,'team_id':str(camp),'team_name':str(camp),'hero_id':105+(camp-1)*5+position,'hero_name':str(position),'position':2,'player_name':f'p{camp}-{position}'} for camp in (1,2) for position in range(5)]
        raw={'match_id':match,'league_id':season,'start_time':time,'bo':1,'score':{'camp1':1,'camp2':0},'match_winner_team_id':'1',
             'teams':[{'match_camp':c,'team_id':str(c)} for c in (1,2)],
             'battles':[{'battle_id':battle,'battle_seq':1,'win_camp':1,'winner_team_id':'1','camp_teams':{str(c):{'team_id':str(c),'match_camp':c} for c in (1,2)},'players':players}]}
        with (path/'matches.jsonl').open('a') as f:f.write(json.dumps(raw)+'\n')
        with (path/'bp_decisions.jsonl').open('a') as f:
            for position in range(1,21):f.write(json.dumps({'league_id':season,'match_id':match,'battle_id':battle,'bp_order':position,'selected_hero_id':105,'legal_hero_ids':[105],'is_peak_battle':False})+'\n')
    return root


def test_cross_season_whole_series_without_target_minimum(tmp_path):
    root=exports(tmp_path/'exports');m=build_rolling_manifest(root)
    assert m['counts']=={'train':20,'validation':10,'calibration':10,'holdout':10}
    assert m['source_seasons']==['A','B']
    assert set(r['season'] for r in m['splits']['validation'])=={'A','B'}
    assert m['series_weights']['A:m000']==.65 and m['series_weights']['B:m049']==1.
    validate_split_manifest(m)
    weights=np.asarray([m['series_weights'][r['season']+':'+r['match_id']] for r in m['splits']['train']])
    assert m['weight_summary']['effective_sample_size']==pytest.approx(weights.sum()**2/(weights**2).sum())


def test_equivalent_offset_times_stay_together():
    rows=[{'start_time':'2025-01-01T01:00:00Z'},{'start_time':'2025-01-01T10:00:00+08:00'},{'start_time':'2025-01-01T02:00:00Z'}]
    early,tail=_take_date_grouped_tail(rows,1)
    assert len(early)==1 and len(tail)==2
    assert event_time('2025-01-01 10:00:00')==event_time('2025-01-01T02:00:00Z')


@pytest.mark.parametrize('kind',['unfinished','partial','duplicate','quality','winner','score','team','battle_id','orphan','illegal_hero','duplicate_order'])
def test_invalid_series_excluded(tmp_path,kind):
    root=exports(tmp_path/'exports');path=root/'B';matches=[json.loads(x) for x in (path/'matches.jsonl').read_text().splitlines()]
    rows=[json.loads(x) for x in (path/'bp_decisions.jsonl').read_text().splitlines()]
    if kind=='unfinished':matches[-1]['match_winner_team_id']=''
    elif kind=='partial':matches[-1]['score']['camp1']=3;matches[-1]['bo']=5
    elif kind=='duplicate':rows.append(copy.deepcopy(rows[-1]))
    elif kind=='quality':rows[-1]['quality_flags']=['bp_player_pick_mismatch']
    elif kind=='illegal_hero':rows[-1]['selected_hero_id']=9999
    elif kind=='duplicate_order':rows[-1]['bp_order']=19
    elif kind=='winner':matches[-1]['battles'][0]['winner_team_id']='2'
    elif kind=='score':matches[-1]['score']={'camp1':0,'camp2':1};matches[-1]['match_winner_team_id']='2'
    elif kind=='team':matches[-1]['battles'][0]['players'][0]['team_id']='3'
    elif kind=='orphan':
        extra=copy.deepcopy(rows[-1]);extra['battle_id']='orphan';rows.append(extra)
    else:
        matches[-1]['bo']=3;matches[-1]['score']['camp1']=2
        second=copy.deepcopy(matches[-1]['battles'][0]);second['battle_seq']=2;matches[-1]['battles'].append(second)
    (path/'matches.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in matches));(path/'bp_decisions.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in rows))
    m=build_rolling_manifest(root)
    assert all(r['match_id']!='m049' for values in m['splits'].values() for r in values)
    assert m['excluded_series'][-1]['match_id']=='m049'


def test_sparse_and_bounded_corpus_deferred(tmp_path):
    root=exports(tmp_path/'exports',35)
    with pytest.raises(DeferredTraining):build_rolling_manifest(root)
    root=exports(tmp_path/'more',50)
    with pytest.raises(DeferredTraining):build_rolling_manifest(root,weighting={'mode':'recent_window','window_days':10,'maximum_age_days':20})


def test_date_weights_and_source_hash_contract(tmp_path):
    root=exports(tmp_path/'exports');m=build_rolling_manifest(root,weighting={'mode':'date_half_life','half_life_days':10})
    assert m['series_weights']['B:m039']==pytest.approx(.5)
    path=root/'A'/'matches.jsonl';path.write_text(path.read_text()+'\n')
    with pytest.raises(ValueError,match='source changed'):training_context_games(m)


def test_weighted_lineup_fit_matches_duplicate_samples_and_intercept():
    x=np.asarray([[0.,1.],[1.,0.],[2.,2.],[3.,1.]])
    y=np.asarray([0.,1.,1.,0.]);weights=np.asarray([1.,2.,3.,4.]);indices=np.repeat(np.arange(4),weights.astype(int))
    a=fit_logistic(x,y,sample_weights=weights,l2=2.);b=fit_logistic(x[indices],y[indices],l2=2.)
    for key in ['means','scales','coefficients','intercept']:np.testing.assert_allclose(a[key],b[key],atol=1e-9)
    trainer=load_trainer();a=trainer.fit_advantage_model(x,y,sample_weights=weights,l2=2.);b=trainer.fit_advantage_model(x[indices],y[indices],l2=2.)
    np.testing.assert_allclose(a['intercept'],b['intercept'],atol=1e-9)


def test_side_swaps_preserve_actual_team_winner_counts(tmp_path):
    root=exports(tmp_path/'exports');path=root/'B'/'matches.jsonl'
    rows=[json.loads(x) for x in path.read_text().splitlines()];battle=rows[-1]['battles'][0]
    battle['camp_teams']={str(c):{'team_id':str(3-c),'match_camp':3-c} for c in (1,2)}
    battle['win_camp']=2
    for player in battle['players']:player['camp']=3-player['camp']
    path.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    manifest=build_rolling_manifest(root)
    assert any(r['match_id']=='m049' for r in manifest['splits']['holdout'])
